from fastapi import HTTPException


def enforce_unit_max_occupancy(unit, adults: int, children: int = 0) -> None:
    """Enforce the existing guest-count semantics: adults plus children."""
    guest_count = adults + children
    if guest_count > unit.max_occupancy:
        raise HTTPException(
            422,
            f"Guest count ({guest_count}) exceeds this unit's maximum occupancy ({unit.max_occupancy})",
        )
