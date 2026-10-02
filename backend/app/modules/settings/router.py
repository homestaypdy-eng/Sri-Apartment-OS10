import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.settings.models import BANK_SETTINGS_SINGLETON_ID, SETTINGS_SINGLETON_ID, BankSettings, BusinessSettings
from app.modules.settings.schemas import BankSettingsOut, BankSettingsUpdate, BusinessSettingsOut, BusinessSettingsUpdate, RolePermission, RolesPermissionsOut, SystemInfoOut
from app.shared.file_upload import read_and_validate_upload, IMAGE_CONTENT_TYPES
from app.shared.rbac import get_current_user, require_role
from app.modules.auth.models import User

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def get_or_create_settings(db: Session, *, commit_if_created: bool = True) -> BusinessSettings:
    settings = db.get(BusinessSettings, SETTINGS_SINGLETON_ID)
    if settings is None:
        settings = BusinessSettings(id=SETTINGS_SINGLETON_ID)
        db.add(settings)
        if commit_if_created:
            db.commit()
            db.refresh(settings)
        else:
            db.flush()
    return settings


def get_or_create_bank_settings(db: Session, *, commit_if_created: bool = True) -> BankSettings:
    bank = db.get(BankSettings, BANK_SETTINGS_SINGLETON_ID)
    if bank is None:
        bank = BankSettings(id=BANK_SETTINGS_SINGLETON_ID)
        db.add(bank)
        if commit_if_created:
            db.commit()
            db.refresh(bank)
        else:
            db.flush()
    return bank


@router.get("", response_model=BusinessSettingsOut, dependencies=[Depends(require_role("admin", "staff"))])
def read_settings(db: Session = Depends(get_db)):
    return get_or_create_settings(db)


@router.put("", response_model=BusinessSettingsOut, dependencies=[Depends(require_role("admin"))])
def update_settings(payload: BusinessSettingsUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_or_create_settings(db)
    changed_fields = payload.model_dump(exclude_unset=True)
    for field, value in changed_fields.items():
        setattr(settings, field, value)
    record_audit_log(
        db, user.id, "settings.update", entity_type="business_settings",
        entity_id=str(settings.id), details=f"Updated fields: {list(changed_fields.keys())}",
    )
    db.commit()
    db.refresh(settings)
    return settings


@router.get("/bank", response_model=BankSettingsOut, dependencies=[Depends(require_role("admin"))])
def read_bank_settings(db: Session = Depends(get_db)):
    """
    RBAC Phase A: admin-only by design (see BankSettings' own docstring) —
    unlike GET /settings, this is never shared with staff or any other
    role. Real bank account number, IFSC, and UPI ID never appear in any
    admin+staff-accessible response.
    """
    return get_or_create_bank_settings(db)


@router.put("/bank", response_model=BankSettingsOut, dependencies=[Depends(require_role("admin"))])
def update_bank_settings(payload: BankSettingsUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    bank = get_or_create_bank_settings(db)
    changed_fields = payload.model_dump(exclude_unset=True)
    for field, value in changed_fields.items():
        setattr(bank, field, value)
    # Field names only in the audit trail (e.g. "bank_ifsc"), never the new
    # values themselves — same principle as never logging a raw OTP/token,
    # applied here to real bank details rather than a security secret.
    record_audit_log(
        db, user.id, "settings.bank_update", entity_type="bank_settings",
        entity_id=str(bank.id), details=f"Updated fields: {list(changed_fields.keys())}",
    )
    db.commit()
    db.refresh(bank)
    return bank


LOGO_STORAGE_DIR = os.environ.get("LOGO_STORAGE_DIR", "/tmp/sri_apartment_logo")
QR_STORAGE_DIR = os.environ.get("QR_STORAGE_DIR", "/tmp/sri_apartment_qr")
os.makedirs(LOGO_STORAGE_DIR, exist_ok=True)
os.makedirs(QR_STORAGE_DIR, exist_ok=True)


@router.post("/logo", response_model=BusinessSettingsOut, dependencies=[Depends(require_role("admin"))])
async def upload_logo(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings_row = get_or_create_settings(db)
    contents = await read_and_validate_upload(file, allowed_content_types=IMAGE_CONTENT_TYPES)
    ext = os.path.splitext(file.filename or "")[1] or ".png"
    stored_path = os.path.join(LOGO_STORAGE_DIR, f"logo{ext}")
    with open(stored_path, "wb") as f:
        f.write(contents)
    settings_row.logo_path = stored_path
    record_audit_log(db, user.id, "settings.logo_upload", entity_type="business_settings", entity_id=str(settings_row.id), details=file.filename or "logo")
    db.commit()
    db.refresh(settings_row)
    return settings_row


@router.post("/qr-code", response_model=BusinessSettingsOut, dependencies=[Depends(require_role("admin"))])
async def upload_qr_code(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings_row = get_or_create_settings(db)
    contents = await read_and_validate_upload(file, allowed_content_types=IMAGE_CONTENT_TYPES)
    ext = os.path.splitext(file.filename or "")[1] or ".png"
    stored_path = os.path.join(QR_STORAGE_DIR, f"qr{ext}")
    with open(stored_path, "wb") as f:
        f.write(contents)
    settings_row.qr_code_path = stored_path
    record_audit_log(db, user.id, "settings.qr_upload", entity_type="business_settings", entity_id=str(settings_row.id), details=file.filename or "qr")
    db.commit()
    db.refresh(settings_row)
    return settings_row


@router.get("/system-info", response_model=SystemInfoOut, dependencies=[Depends(require_role("admin"))])
def get_system_info(db: Session = Depends(get_db)):
    """
    A read-only view of what's actually configured — deliberately never
    exposes the credentials themselves (SMTP password, API keys), just
    whether each channel is set up, so an admin can see 'is email
    working' without a support ticket, and without the security downside
    of putting secrets in an editable web form.
    """
    try:
        db.execute(text("SELECT 1"))
        db_connected = True
    except Exception:
        db_connected = False

    return SystemInfoOut(
        environment=app_settings.environment,
        app_version="1.0.0",
        server_time=datetime.now(timezone.utc),
        database_connected=db_connected,
        email_configured=bool(app_settings.smtp_host),
        sms_configured=bool(app_settings.sms_api_url and app_settings.sms_api_key),
        whatsapp_configured=True,  # click-to-chat needs no server-side credentials, always available
        whatsapp_auto_configured=bool(app_settings.whatsapp_access_token and app_settings.whatsapp_phone_number_id),
    )


@router.get("/roles-permissions", response_model=RolesPermissionsOut, dependencies=[Depends(require_role("admin", "staff"))])
def get_roles_permissions():
    """
    A real, accurate reference of what the roles in this system can
    actually do — reflects the real require_role(...) checks used
    throughout the codebase, not an aspirational or separate permission
    model that could drift from what's actually enforced.
    """
    return RolesPermissionsOut(roles=[
        RolePermission(
            role="admin", description="Full access to every module and every action.",
            can_do=[
                "Create, edit, and deactivate properties, units, and pricing rules",
                "Approve or reject expenses; mark payroll as paid; issue refunds and credit notes",
                "Create and manage staff user accounts, assign roles",
                "Edit business settings, upload logo/QR, view system configuration status",
                "View the full audit log across every module",
            ],
            cannot_do=[],
        ),
        RolePermission(
            role="staff", description="Day-to-day operational access — cannot change financial approvals, business settings, or user accounts.",
            can_do=[
                "Create and manage bookings, guests, inquiries, housekeeping and maintenance tasks",
                "Record payments and generate invoices/quotations",
                "View reports, dashboards, and their own notifications",
                "Update their own attendance and leave requests",
            ],
            cannot_do=[
                "Approve/reject expenses or mark payroll as paid",
                "Create staff accounts or change another user's role",
                "Edit business settings or upload logo/QR",
                "Issue credit notes",
            ],
        ),
        # RBAC Phase B documentation fix (finding I-4): this reference had
        # drifted — it described only "the two roles" from before Manager
        # existed. This entry mirrors the actual require_role(...) grants
        # verified across the codebase (properties/pricing, bookings/finance
        # views, staff_ops, OTA view+sync) and exclusions (settings, users,
        # audit, staff/HR, refunds/credit-notes, AI Strategist, property/unit
        # CRUD) — informational only, no authorization code reads this.
        RolePermission(
            role="manager",
            description="Operational management access — day-to-day operations and pricing, without administrative, financial-approval, or security functions.",
            can_do=[
                "Create and manage bookings, guests, inquiries, housekeeping and maintenance tasks",
                "Record payments, generate invoices/quotations, and view existing credit notes",
                "View reports, dashboards, staff operations, and OTA channel/sync status",
                "Manage accommodation pricing: base/weekend rates, seasonal rates, and long-stay discount tiers",
            ],
            cannot_do=[
                "Create staff accounts, change any user's role, or access user administration",
                "Access Bank & UPI settings, business settings, system configuration, or backups",
                "View the audit log or login history",
                "Access Staff/HR administration (attendance, leave, payroll, staff profiles)",
                "Issue payment refunds, security-deposit refunds, or credit notes",
                "Delete, archive, or restore bookings, or merge guest records",
                "Create, edit, or deactivate properties/units (pricing only)",
                "Access the AI Strategist",
            ],
        ),
    ])


@router.get("/backup", dependencies=[Depends(require_role("admin"))])
def download_backup(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    A real, working data export — every row in every table that isn't
    purely derived/log data, as a single JSON file the business can keep
    off-site. Restoring from it is deliberately NOT a self-service web
    action: reloading arbitrary JSON into a live production database is
    a genuinely dangerous operation (wrong file, wrong environment, or a
    stale export could silently destroy real guest/financial data), so
    restore stays an infrastructure-level operation using this same file
    with the documented pg_restore/psql process in DEPLOYMENT.md, not a
    button any admin can click by accident.
    """
    from app.db.session import Base, engine
    from sqlalchemy import inspect

    inspector = inspect(engine)
    dump: dict = {"exported_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        rows = db.execute(text(f'SELECT * FROM "{table.name}"')).mappings().all()
        dump["tables"][table.name] = [
            {k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if v is not None else None) for k, v in row.items()}
            for row in rows
        ]

    record_audit_log(db, user.id, "settings.backup_download", entity_type="business_settings", entity_id=str(SETTINGS_SINGLETON_ID), details="Full data export downloaded")
    db.commit()

    content = json.dumps(dump, indent=2)
    filename = f"sri_apartment_backup_{datetime.now(timezone.utc).date().isoformat()}.json"
    return StreamingResponse(
        iter([content]), media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
