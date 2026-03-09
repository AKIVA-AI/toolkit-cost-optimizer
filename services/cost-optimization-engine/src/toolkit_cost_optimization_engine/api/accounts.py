"""Cloud Account CRUD router."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from ..api.schemas import (
    CloudAccount,
    CloudAccountCreate,
    CloudAccountUpdate,
    PaginatedAccounts,
)
from ..core.credential_encryption import encrypt_credential
from ..core.database import get_db_session
from ..models.models import CloudAccount as CloudAccountModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/accounts", tags=["Cloud Accounts"])


@router.post("", response_model=CloudAccount)
async def create_cloud_account(account_data: CloudAccountCreate):
    """Create a new cloud account."""
    try:
        async with get_db_session() as session:
            existing = await session.execute(
                select(CloudAccountModel).where(
                    CloudAccountModel.provider == account_data.provider,
                    CloudAccountModel.account_id == account_data.account_id,
                    CloudAccountModel.region == account_data.region,
                ),
            )

            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cloud account with this provider,"
                        " account ID, and region already exists"
                    ),
                )

            account_dict = account_data.model_dump()
            if account_dict.get("access_key"):
                account_dict["access_key"] = encrypt_credential(account_dict["access_key"])
            if account_dict.get("secret_key"):
                account_dict["secret_key"] = encrypt_credential(account_dict["secret_key"])
            account = CloudAccountModel(**account_dict)
            session.add(account)
            await session.commit()
            await session.refresh(account)

            return CloudAccount.model_validate(account)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create cloud account: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to create cloud account",
        ) from e


@router.get("", response_model=PaginatedAccounts)
async def list_cloud_accounts(
    provider: str | None = Query(None, pattern=r"^(aws|azure|gcp)$"),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List cloud accounts with pagination metadata."""
    try:
        async with get_db_session() as session:
            base_query = select(CloudAccountModel)

            if provider:
                base_query = base_query.where(CloudAccountModel.provider == provider)
            if is_active is not None:
                base_query = base_query.where(CloudAccountModel.is_active == is_active)

            count_query = select(func.count()).select_from(base_query.subquery())
            total_result = await session.execute(count_query)
            total = total_result.scalar() or 0

            paginated = base_query.offset((page - 1) * page_size).limit(page_size)
            result = await session.execute(paginated)
            accounts = result.scalars().all()

            total_pages = max((total + page_size - 1) // page_size, 1)

            return PaginatedAccounts(
                items=[CloudAccount.model_validate(a) for a in accounts],
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
            )

    except Exception as e:
        logger.error(f"Failed to list cloud accounts: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to list cloud accounts",
        ) from e


@router.get("/{account_id}", response_model=CloudAccount)
async def get_cloud_account(account_id: str):
    """Get a specific cloud account."""
    try:
        async with get_db_session() as session:
            result = await session.execute(
                select(CloudAccountModel).where(CloudAccountModel.id == account_id),
            )
            account = result.scalar_one_or_none()

            if not account:
                raise HTTPException(status_code=404, detail="Cloud account not found")

            return CloudAccount.model_validate(account)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cloud account: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get cloud account",
        ) from e


@router.put("/{account_id}", response_model=CloudAccount)
async def update_cloud_account(account_id: str, account_data: CloudAccountUpdate):
    """Update a cloud account."""
    try:
        async with get_db_session() as session:
            result = await session.execute(
                select(CloudAccountModel).where(CloudAccountModel.id == account_id),
            )
            account = result.scalar_one_or_none()

            if not account:
                raise HTTPException(status_code=404, detail="Cloud account not found")

            update_data = account_data.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                if field in ("access_key", "secret_key") and value:
                    value = encrypt_credential(value)
                setattr(account, field, value)

            await session.commit()
            await session.refresh(account)

            return CloudAccount.model_validate(account)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update cloud account: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to update cloud account",
        ) from e


@router.delete("/{account_id}")
async def delete_cloud_account(account_id: str):
    """Delete a cloud account."""
    try:
        async with get_db_session() as session:
            result = await session.execute(
                select(CloudAccountModel).where(CloudAccountModel.id == account_id),
            )
            account = result.scalar_one_or_none()

            if not account:
                raise HTTPException(status_code=404, detail="Cloud account not found")

            await session.delete(account)
            await session.commit()

            return {"message": "Cloud account deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete cloud account: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to delete cloud account",
        ) from e
