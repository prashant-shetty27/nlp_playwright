"""
api/routes/users.py — sign-in and user management.

POST /users/login          {username, password}      → the user (no secrets)
GET  /users/setup          → {needs_setup: bool}     (no users exist yet)
POST /users/setup          first admin, only while no user exists
GET  /users                list (any signed-in user)
POST /users                create (admin)
PUT  /users/{name}         update role / name / email / active / password (admin, or self for name/password)
DELETE /users/{name}       delete (admin; never the last admin)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import acting_user, require
from core import users

router = APIRouter(prefix="/accounts", tags=["users"])


class Login(BaseModel):
    username: str
    password: str


class NewUser(BaseModel):
    username: str
    password: str
    name: str = ""
    email: str = ""
    role: str = "editor"


class UserPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None


@router.post("/login")
def login(body: Login):
    u = users.verify(body.username, body.password)
    if not u:
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    return u


@router.get("/setup")
def setup_needed():
    return {"needs_setup": not users.has_users()}


@router.post("/setup", status_code=201)
def setup(body: NewUser):
    if users.has_users():
        raise HTTPException(status_code=409, detail="Setup is already done — sign in instead.")
    try:
        return users.create(body.username, body.password, name=body.name,
                            email=body.email, role="admin")
    except users.UserError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("")
def list_all(user: str = Depends(acting_user)):
    return {"users": users.list_users(), "me": users.get(user) if user else None}


@router.post("", status_code=201)
def create(body: NewUser, user: str = Depends(acting_user)):
    require(user, "admin")
    try:
        return users.create(body.username, body.password, name=body.name, email=body.email,
                            role=body.role, created_by=user)
    except users.UserError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.put("/{name}")
def update(name: str, body: UserPatch, user: str = Depends(acting_user)):
    self_edit = user and user == name.strip().lower()
    admin_only = body.role is not None or body.active is not None or body.email is not None
    if not self_edit or admin_only:
        require(user, "admin")
    try:
        return users.update(name, name=body.name, email=body.email, role=body.role,
                            active=body.active, password=body.password)
    except users.UserError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete("/{name}")
def delete(name: str, user: str = Depends(acting_user)):
    require(user, "admin")
    if user and user == name.strip().lower():
        raise HTTPException(status_code=422, detail="You cannot delete your own account.")
    try:
        users.delete(name)
    except users.UserError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"deleted": name}
