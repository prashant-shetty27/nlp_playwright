"""
api/routes/run_configs.py — saved run configurations (see core/run_configs.py).

GET    /run-configs?module=     this module's configurations: mine + shared
POST   /run-configs             save (new, or update with id) — owner/admin only
DELETE /run-configs/{id}        delete — owner/admin only
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import acting_user, require
from core import run_configs

router = APIRouter(prefix="/run-configs", tags=["run-configs"])


class ConfigBody(BaseModel):
    name: str
    module: str
    settings: dict = {}
    shared: bool = False
    id: str = ""


def _admin(user: str) -> bool:
    try:
        from core.users import can
        return can(user, "admin")
    except Exception:  # noqa: BLE001
        return False


@router.get("")
def list_configs(module: str = "", user: str = Depends(acting_user)):
    return {"configs": run_configs.visible(module, user)}


@router.post("")
def save_config(body: ConfigBody, user: str = Depends(acting_user)):
    require(user, "run")
    try:
        return run_configs.save(body.name, body.module, user, body.settings, body.shared,
                                body.id, is_admin=_admin(user))
    except run_configs.RunConfigError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete("/{cid}")
def delete_config(cid: str, user: str = Depends(acting_user)):
    require(user, "run")
    try:
        run_configs.delete(cid, user, is_admin=_admin(user))
    except run_configs.RunConfigError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"deleted": cid}
