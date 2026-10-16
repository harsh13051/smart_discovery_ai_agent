"""FastAPI application: auth, conversations, restaurant search, favourites, comparison, profile."""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import tools as tool_mod
from .agent import AgentResult, run_agent, run_pipeline
from .db import (
    AuthSession, Conversation, Favourite, Message, RestaurantCache, User, jloads, make_engine,
    make_session_factory, run_migrations, utcnow,
)
from .llm import AnthropicClient, OpenAICompatClient
from .prefs import PrefError, prefs_from_args
from .providers.cache import DbCache
from .providers.demo import DemoProvider
from .providers.osm import OSMProvider, ProviderError
from .schema import Restaurant
from .security import RateLimiter, hash_password, new_token, token_hash, validate_registration, verify_password
from .settings import AppSettings, load_settings

log = logging.getLogger("discovery.api")
SESSION_COOKIE, CSRF_COOKIE, CSRF_HEADER = "sid", "csrf_token", "x-csrf-token"
FAVOURITE_STALE_DAYS = 30
DUMMY_HASH = hash_password("not-a-real-password-0")


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------- schemas
class RegisterBody(BaseModel):
    name: str = Field(max_length=200)
    email: str = Field(max_length=320)
    password: str = Field(max_length=500)


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=500)


class MessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    use_demo: bool = False


class TitleBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class FavBody(BaseModel):
    restaurant_id: str = Field(min_length=3, max_length=80)


class CompareBody(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=5)


class ProfileBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    default_location: Optional[str] = Field(default=None, max_length=120)
    diet: Optional[str] = None
    budget_per_person: Optional[float] = Field(default=None, gt=0, le=100000)
    cuisines: Optional[list[str]] = Field(default=None, max_length=8)


# ---------------------------------------------------------------- app factory
def create_app(settings: Optional[AppSettings] = None, osm: Optional[OSMProvider] = None,
               llm_client: Optional[AnthropicClient] = None, demo: Optional[DemoProvider] = None) -> FastAPI:
    s = settings or load_settings()
    engine = make_engine(s.database_url)
    run_migrations(engine)
    sf = make_session_factory(engine)
    osm = osm or OSMProvider(s, DbCache(sf))
    if demo is None and s.allow_demo_data:
        demo = DemoProvider()
    if llm_client is None and s.llm_enabled:
        llm_client = (OpenAICompatClient(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_timeout_s)
                      if s.llm_provider == "openai_compatible"
                      else AnthropicClient(s.anthropic_api_key, s.llm_model, s.llm_timeout_s))
    login_limiter = RateLimiter(8, 60)
    action_limiter = RateLimiter(40, 60)

    app = FastAPI(title="Smart Restaurant Discovery", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings, app.state.sf, app.state.engine = s, sf, engine
    app.state.limiters = (login_limiter, action_limiter)

    # ---- middleware ------------------------------------------------------
    @app.middleware("http")
    async def security(request: Request, call_next):  # noqa: ANN001
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
            cookie, header = request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)
            if not cookie or not header or not hmac.compare_digest(cookie, header):
                return JSONResponse({"detail": "CSRF token missing or invalid."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        response.headers.setdefault("Cache-Control", "no-store" if request.url.path.startswith("/api/") else "no-cache")  # static: revalidate
        return response

    # ---- dependencies ----------------------------------------------------
    def get_db():
        with sf() as db:
            yield db

    def current_user(db: Session = Depends(get_db), sid: Optional[str] = Cookie(default=None)) -> User:
        if not sid:
            raise HTTPException(401, "Not authenticated.")
        row = db.get(AuthSession, token_hash(sid))
        if row is None:
            raise HTTPException(401, "Not authenticated.")
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if exp < utcnow():
            db.delete(row)
            db.commit()
            raise HTTPException(401, "Session expired.")
        user = db.get(User, row.user_id)
        if user is None:
            raise HTTPException(401, "Not authenticated.")
        return user

    def set_session(response: Response, db: Session, user: User) -> None:
        token = new_token()
        db.add(AuthSession(token_hash=token_hash(token), user_id=user.id,
                           expires_at=utcnow() + timedelta(days=s.session_days)))
        db.commit()
        age = s.session_days * 86400
        response.set_cookie(SESSION_COOKIE, token, max_age=age, httponly=True, samesite="lax", secure=s.cookie_secure, path="/")

    def user_json(u: User) -> dict[str, Any]:
        return {"id": u.id, "name": u.name, "email": u.email, "created_at": iso(u.created_at),
                "preferences": jloads(u.preferences, {})}

    def owned_conversation(db: Session, user: User, cid: str) -> Conversation:
        conv = db.get(Conversation, cid)
        if conv is None or conv.user_id != user.id:
            raise HTTPException(404, "Conversation not found.")  # same answer for "not yours"
        return conv

    def cache_lookup_factory(db: Session):
        def lookup(rid: str) -> Optional[Restaurant]:
            row = db.get(RestaurantCache, rid)
            return Restaurant.from_dict(json.loads(row.data)) if row else None
        return lookup

    def cache_store_factory():
        def store(items: list[Restaurant]) -> None:
            with sf() as db:
                for r in items:
                    row = db.get(RestaurantCache, r.id)
                    data = json.dumps({k: v for k, v in r.to_dict().items() if k != "open_now"})
                    if row:
                        row.data, row.fetched_at = data, utcnow()
                    else:
                        db.add(RestaurantCache(id=r.id, data=data, fetched_at=utcnow()))
                db.commit()
        return store

    def make_ctx(db: Session, user: User, conv_context: Optional[dict] = None,
                 lat: Optional[float] = None, lon: Optional[float] = None) -> tool_mod.ToolContext:
        return tool_mod.ToolContext(
            osm=osm, demo=demo, cache_lookup=cache_lookup_factory(db), cache_store=cache_store_factory(),
            saved_prefs=jloads(user.preferences, {}), conversation_context=conv_context or {},
            user_location=(lat, lon) if lat is not None and lon is not None else None)

    # ---- status ----------------------------------------------------------
    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {"llm": {"configured": s.llm_enabled, "provider": s.llm_provider if s.llm_enabled else None,
                        "model": s.llm_model if s.llm_enabled else None,
                        "mode": "llm" if s.llm_enabled else "rule-based fallback"},
                "restaurant_data": {"provider": "OpenStreetMap (Overpass + Nominatim)", "needs_key": False,
                                    "contact_configured": bool(s.osm_contact)},
                "demo_data_available": demo is not None}

    # ---- auth ------------------------------------------------------------
    @app.get("/api/auth/csrf")
    def csrf(response: Response, csrf_token: Optional[str] = Cookie(default=None)) -> dict[str, str]:
        token = csrf_token or secrets.token_urlsafe(24)
        response.set_cookie(CSRF_COOKIE, token, httponly=False, samesite="lax", secure=s.cookie_secure,
                            path="/", max_age=30 * 86400)
        return {"csrf_token": token}

    @app.post("/api/auth/register", status_code=201)
    def register(body: RegisterBody, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
        email = body.email.strip().lower()
        errors = validate_registration(body.name, email, body.password)
        if errors:
            raise HTTPException(422, {"message": "Please fix the highlighted fields.", "fields": errors})
        if db.scalar(select(User).where(User.email == email)):
            raise HTTPException(409, {"message": "An account with this email already exists.", "fields": {"email": "Already registered."}})
        user = User(name=body.name.strip(), email=email, password_hash=hash_password(body.password))
        db.add(user)
        db.commit()
        set_session(response, db, user)
        return {"user": user_json(user)}

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
        email = body.email.strip().lower()
        key = f"{request.client.host if request.client else '?'}|{email}"
        if not login_limiter.allow(key):
            raise HTTPException(429, "Too many login attempts. Please wait a minute and try again.")
        user = db.scalar(select(User).where(User.email == email))
        ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
        if not user or not ok:
            raise HTTPException(401, "Invalid email or password.")
        set_session(response, db, user)
        return {"user": user_json(user)}

    @app.post("/api/auth/logout")
    def logout(response: Response, db: Session = Depends(get_db), sid: Optional[str] = Cookie(default=None)) -> dict[str, bool]:
        if sid:
            row = db.get(AuthSession, token_hash(sid))
            if row:
                db.delete(row)
                db.commit()
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: User = Depends(current_user)) -> dict[str, Any]:
        return {"user": user_json(user)}

    # ---- profile ---------------------------------------------------------
    @app.put("/api/profile")
    def update_profile(body: ProfileBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
        prefs = jloads(user.preferences, {})
        data = body.model_dump(exclude_unset=True)
        if "name" in data and data["name"] is not None:
            if not data["name"].strip():
                raise HTTPException(422, "Name cannot be empty.")
            user.name = data.pop("name").strip()
        else:
            data.pop("name", None)
        if data.get("diet") not in (None, "any", "vegetarian", "vegan", "jain"):
            raise HTTPException(422, "diet must be any, vegetarian, vegan or jain.")
        for k, v in data.items():
            if v in (None, "", []):
                prefs.pop(k, None)
            else:
                prefs[k] = v.strip() if isinstance(v, str) else v
        user.preferences = json.dumps(prefs)
        db.merge(user)
        db.commit()
        return {"user": user_json(user)}

    # ---- conversations ---------------------------------------------------
    def conv_json(c: Conversation) -> dict[str, Any]:
        return {"id": c.id, "title": c.title, "created_at": iso(c.created_at), "updated_at": iso(c.updated_at)}

    def msg_json(m: Message) -> dict[str, Any]:
        return {"id": m.id, "role": m.role, "content": m.content, "created_at": iso(m.created_at),
                "payload": jloads(m.payload)}

    @app.get("/api/conversations")
    def list_conversations(q: Optional[str] = Query(default=None, max_length=100), limit: int = Query(30, ge=1, le=100),
                           before: Optional[str] = None, user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict[str, Any]:
        stmt = select(Conversation).where(Conversation.user_id == user.id)
        if q and q.strip():
            like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            msg_hit = select(Message.conversation_id).where(Message.content.like(like, escape="\\"))
            stmt = stmt.where(or_(Conversation.title.like(like, escape="\\"), Conversation.id.in_(msg_hit)))
        if before:
            try:
                stmt = stmt.where(Conversation.updated_at < datetime.fromisoformat(before))
            except ValueError:
                raise HTTPException(422, "Invalid 'before' timestamp.")
        rows = db.scalars(stmt.order_by(Conversation.updated_at.desc(), Conversation.id).limit(limit + 1)).all()
        return {"items": [conv_json(c) for c in rows[:limit]], "has_more": len(rows) > limit}

    @app.post("/api/conversations", status_code=201)
    def create_conversation(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
        conv = Conversation(id=str(uuid.uuid4()), user_id=user.id, title="New chat")
        db.add(conv)
        db.commit()
        return conv_json(conv)

    @app.get("/api/conversations/{cid}")
    def get_conversation(cid: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
        return conv_json(owned_conversation(db, user, cid))

    @app.get("/api/conversations/{cid}/messages")
    def list_messages(cid: str, limit: int = Query(50, ge=1, le=200), before_id: Optional[int] = None,
                      user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
        owned_conversation(db, user, cid)
        stmt = select(Message).where(Message.conversation_id == cid)
        if before_id:
            stmt = stmt.where(Message.id < before_id)
        rows = list(db.scalars(stmt.order_by(Message.id.desc()).limit(limit + 1)).all())
        has_more = len(rows) > limit
        rows = rows[:limit][::-1]  # oldest → newest
        return {"items": [msg_json(m) for m in rows], "has_more": has_more}

    @app.patch("/api/conversations/{cid}")
    def rename_conversation(cid: str, body: TitleBody, user: User = Depends(current_user),
                            db: Session = Depends(get_db)) -> dict[str, Any]:
        conv = owned_conversation(db, user, cid)
        title = body.title.strip()
        if not title:
            raise HTTPException(422, "Title cannot be empty.")
        conv.title = title
        db.commit()
        return conv_json(conv)

    @app.delete("/api/conversations/{cid}")
    def delete_conversation(cid: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
        conv = owned_conversation(db, user, cid)
        db.delete(conv)  # cascades to this conversation's messages only
        db.commit()
        return {"ok": True}

    @app.post("/api/conversations/{cid}/messages")
    def send_message(cid: str, body: MessageBody, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
        conv = owned_conversation(db, user, cid)
        if not action_limiter.allow(f"chat:{user.id}"):
            raise HTTPException(429, "You're sending messages too quickly. Please wait a moment.")
        text = body.content.strip()
        if not text:
            raise HTTPException(422, "Message cannot be empty.")
        first = db.scalar(select(Message.id).where(Message.conversation_id == cid).limit(1)) is None
        user_msg = Message(conversation_id=cid, role="user", content=text)
        db.add(user_msg)
        if first:
            conv.title = (text[:57] + "…") if len(text) > 58 else text
        conv.updated_at = utcnow()
        db.commit()

        history_rows = db.scalars(select(Message).where(Message.conversation_id == cid, Message.id < user_msg.id)
                                  .order_by(Message.id.desc()).limit(12)).all()[::-1]
        history = [{"role": m.role, "content": m.content} for m in history_rows]
        context = jloads(conv.context, {})
        ctx = make_ctx(db, user, context, body.lat, body.lon)
        if body.use_demo:
            ctx.prefs.use_demo = True
        try:
            result: AgentResult = run_agent(ctx, text, history, llm_client)
        except Exception:  # noqa: BLE001 - never lose the user's message to an agent crash
            log.exception("agent crashed")
            result = AgentResult("provider_error", "Something went wrong while handling that request. "
                                 "Your message was saved; please try again.", mode="rule-based")
        payload = {k: v for k, v in result.to_dict().items() if k != "message"}
        ai_msg = Message(conversation_id=cid, role="assistant", content=result.message, payload=json.dumps(payload, default=str))
        db.add(ai_msg)
        new_ctx = dict(context)
        if result.preferences:
            new_ctx["prefs"] = {k: v for k, v in result.preferences.items() if k not in ("budget",)}
        new_ctx["last_shortlist"] = [{"id": c["id"], "name": c["name"]} for c in result.cards][:10]
        new_ctx["awaiting"] = "location" if result.status == "needs_clarification" else None
        conv.context = json.dumps(new_ctx, default=str)
        conv.updated_at = utcnow()
        db.commit()
        return {"conversation": conv_json(conv), "user_message": msg_json(user_msg), "assistant_message": msg_json(ai_msg)}

    # ---- restaurants -----------------------------------------------------
    @app.get("/api/restaurants/search")
    def search(location: Optional[str] = Query(default=None, max_length=200), lat: Optional[float] = Query(default=None, ge=-90, le=90),
               lon: Optional[float] = Query(default=None, ge=-180, le=180), cuisines: Optional[str] = Query(default=None, max_length=200),
               diet: str = "any", budget: Optional[float] = Query(default=None, gt=0), budget_type: str = "total",
               party: Optional[int] = Query(default=None, ge=1, le=50), amenities: Optional[str] = Query(default=None, max_length=200),
               min_rating: Optional[float] = Query(default=None, ge=0, le=5), open_now: bool = False,
               kind: Optional[str] = None, radius_km: float = Query(2.5, ge=0.2, le=8), demo_data: bool = False,
               page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=30),
               user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
        if not action_limiter.allow(f"search:{user.id}"):
            raise HTTPException(429, "Too many searches. Please wait a moment.")
        ctx = make_ctx(db, user, None, lat, lon)
        try:
            ctx.prefs = prefs_from_args({
                "location_text": location, "lat": lat, "lon": lon, "diet": diet, "budget_amount": budget,
                "budget_type": budget_type, "party_size": party, "min_rating": min_rating, "open_now": open_now,
                "kind": kind or None, "radius_km": radius_km, "use_demo": demo_data,
                "cuisines": [c for c in (cuisines or "").split(",") if c.strip()],
                "amenities": [a for a in (amenities or "").split(",") if a.strip()]})
        except PrefError as exc:
            raise HTTPException(422, str(exc))
        if not (ctx.prefs.location_text or ctx.prefs.lat is not None):
            raise HTTPException(422, "Enter a location (or share your location) to search.")
        result = run_pipeline(ctx, top_n=page * page_size + 1)  # +1 reveals whether another page exists
        if result.status in ("provider_error",):
            raise HTTPException(503, result.message)
        if result.status == "invalid_input":
            raise HTTPException(422, result.message)
        start = (page - 1) * page_size
        cards = result.cards[start:start + page_size]
        return {"status": result.status, "message": result.message, "cards": cards, "preferences": result.preferences,
                "source": result.source, "notes": result.notes, "alternatives": result.alternatives,
                "page": page, "has_more": len(result.cards) > page * page_size,
                "trace": result.trace}

    def load_restaurant(db: Session, rid: str) -> Optional[Restaurant]:
        r = cache_lookup_factory(db)(rid)
        if r is None and rid.startswith("osm:"):
            try:
                r = osm.lookup(rid[4:])
            except ProviderError:
                return None
            if r:
                cache_store_factory()([r])
        return r

    @app.get("/api/restaurant")
    def restaurant_detail(id: str = Query(min_length=3, max_length=80), user: User = Depends(current_user),
                          db: Session = Depends(get_db)) -> dict[str, Any]:
        r = load_restaurant(db, id)
        if r is None:
            raise HTTPException(404, "Restaurant not found. Run a search again to refresh it.")
        data = r.to_dict()
        fav = db.scalar(select(Favourite).where(Favourite.user_id == user.id, Favourite.restaurant_id == id))
        data["is_favourite"] = fav is not None
        return {"restaurant": data}

    @app.post("/api/compare")
    def compare(body: CompareBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
        ctx = make_ctx(db, user)
        for rid in body.ids:
            r = load_restaurant(db, rid)
            if r:
                ctx.candidates[rid] = r
        result = tool_mod.execute_tool(ctx, "compare_restaurants", {"restaurant_ids": body.ids})
        if "error" in result:
            raise HTTPException(404, result["error"])
        return ctx.comparison or {}

    # ---- favourites ------------------------------------------------------
    def fav_json(f: Favourite) -> dict[str, Any]:
        snap = jloads(f.snapshot, {})
        age = utcnow() - (f.created_at if f.created_at.tzinfo else f.created_at.replace(tzinfo=timezone.utc))
        return {"id": f.id, "restaurant_id": f.restaurant_id, "provider": f.restaurant_provider_id,
                "saved_at": iso(f.created_at), "stale": age > timedelta(days=FAVOURITE_STALE_DAYS),
                "restaurant": Restaurant.from_dict(snap).to_dict() if snap else None}

    @app.get("/api/favourites")
    def list_favourites(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
        rows = db.scalars(select(Favourite).where(Favourite.user_id == user.id).order_by(Favourite.created_at.desc())).all()
        return {"items": [fav_json(f) for f in rows]}

    @app.post("/api/favourites", status_code=201)
    def add_favourite(body: FavBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
        existing = db.scalar(select(Favourite).where(Favourite.user_id == user.id, Favourite.restaurant_id == body.restaurant_id))
        if existing:
            return fav_json(existing)
        r = load_restaurant(db, body.restaurant_id)
        if r is None:
            raise HTTPException(404, "Restaurant not found. Run a search again, then save it.")
        fav = Favourite(user_id=user.id, restaurant_provider_id=r.provider, restaurant_id=r.id,
                        snapshot=json.dumps({k: v for k, v in r.to_dict().items() if k != "open_now"}))
        db.add(fav)
        db.commit()
        return fav_json(fav)

    @app.delete("/api/favourites")
    def remove_favourite(id: str = Query(min_length=3, max_length=80), user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict[str, bool]:
        fav = db.scalar(select(Favourite).where(Favourite.user_id == user.id, Favourite.restaurant_id == id))
        if fav is None:
            raise HTTPException(404, "Not in your favourites.")
        db.delete(fav)
        db.commit()
        return {"ok": True}

    @app.exception_handler(HTTPException)
    async def http_exc(_: Request, exc: HTTPException):  # noqa: ANN001
        detail = exc.detail
        body = detail if isinstance(detail, dict) else {"message": detail}
        return JSONResponse({"detail": body.get("message"), **body}, status_code=exc.status_code, headers=exc.headers)

    if s.static_dir.exists():
        app.mount("/", StaticFiles(directory=str(s.static_dir), html=True), name="web")
    return app
