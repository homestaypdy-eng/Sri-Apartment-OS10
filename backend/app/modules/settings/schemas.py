from datetime import datetime

from pydantic import BaseModel


class BusinessSettingsUpdate(BaseModel):
    company_name: str | None = None
    tagline: str | None = None
    address: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    gst_number: str | None = None
    udyam_number: str | None = None
    state_code: str | None = None
    signatory_name: str | None = None
    signatory_title: str | None = None
    invoice_terms: str | None = None
    quotation_terms: str | None = None
    house_rules: str | None = None
    cancellation_policy: str | None = None
    default_gst_rate_percent: float | None = None
    theme_color: str | None = None
    whatsapp_business_number: str | None = None
    secondary_contact_email: str | None = None
    secondary_whatsapp_number: str | None = None


class BusinessSettingsOut(BaseModel):
    company_name: str
    tagline: str
    address: str
    contact_phone: str
    contact_email: str
    gst_number: str
    udyam_number: str
    state_code: str
    signatory_name: str
    signatory_title: str
    logo_path: str | None
    qr_code_path: str | None
    invoice_terms: str
    quotation_terms: str
    house_rules: str
    cancellation_policy: str
    default_gst_rate_percent: float | None
    theme_color: str
    whatsapp_business_number: str | None
    secondary_contact_email: str | None
    secondary_whatsapp_number: str | None
    updated_at: datetime

    class Config:
        from_attributes = True


class BankSettingsUpdate(BaseModel):
    bank_name: str | None = None
    bank_account_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    bank_branch: str | None = None
    upi_id: str | None = None


class BankSettingsOut(BaseModel):
    bank_name: str
    bank_account_name: str
    bank_account_number: str
    bank_ifsc: str
    bank_branch: str
    upi_id: str
    updated_at: datetime

    class Config:
        from_attributes = True


class SystemInfoOut(BaseModel):
    environment: str
    app_version: str
    server_time: datetime
    database_connected: bool
    email_configured: bool
    sms_configured: bool
    whatsapp_configured: bool
    # Distinct from whatsapp_configured above (which describes the manual
    # wa.me click-to-chat link, always available with no credentials) —
    # this reflects whether a real, automatic WhatsApp Business Cloud API
    # send is actually possible right now.
    whatsapp_auto_configured: bool


class RolePermission(BaseModel):
    role: str
    description: str
    can_do: list[str]
    cannot_do: list[str]


class RolesPermissionsOut(BaseModel):
    roles: list[RolePermission]
