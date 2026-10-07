import uuid
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.confirmations import require_confirmed_action
from app.api.dependencies import admin_user, current_user
from app.api.v1.media import PhotoVariant, data_url_media_response
from app.db.session import get_db_session
from app.models import Group, Person, User, Vehicle
from app.schemas.directory import (
    CreateGroupRequest,
    CreatePersonRequest,
    CreateVehicleRequest,
    DirectoryConfirmationRequest,
    DirectoryPage,
    GroupResponse,
    PersonResponse,
    UpdateGroupRequest,
    UpdatePersonRequest,
    UpdateVehicleRequest,
    VehicleResponse,
)
from app.services.directory import groups, people, reads, vehicles
from app.services.directory.errors import DirectoryOperationError
from app.services.vehicle_information_contracts import MotHistoryPage

router = APIRouter()


def directory_errors[**Params, Result](
    operation: Callable[Params, Awaitable[Result]],
) -> Callable[Params, Awaitable[Result]]:
    @wraps(operation)
    async def adapted(*args: Params.args, **kwargs: Params.kwargs) -> Result:
        try:
            return await operation(*args, **kwargs)
        except DirectoryOperationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return adapted


@router.post("/people", response_model=PersonResponse, status_code=status.HTTP_201_CREATED)
@directory_errors
async def add_person(
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    request: CreatePersonRequest,
) -> PersonResponse:
    await require_confirmed_action(
        session,
        user=user,
        action="person.create",
        payload=request.model_dump(
            mode="json", exclude={"confirmation_token"}, exclude_none=True, exclude_unset=True
        ),
        confirmation_token=request.confirmation_token,
    )
    return await people.add_person(request=request, user=user, session=session)


@router.patch("/people/{person_id}", response_model=PersonResponse)
@directory_errors
async def update_person(
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    person_id: uuid.UUID,
    request: UpdatePersonRequest,
) -> PersonResponse:
    await reads.ensure_exists(session, Person, person_id)
    confirmation_payload = request.model_dump(
        mode="json",
        exclude={"confirmation_token"},
        exclude_none=True,
        exclude_unset=True,
    )
    confirmation_payload["person_id"] = str(person_id)
    await require_confirmed_action(
        session,
        user=user,
        action="person.update",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    return await people.update_person(
        person_id=person_id, request=request, user=user, session=session
    )


@router.post("/vehicles", response_model=VehicleResponse, status_code=status.HTTP_201_CREATED)
@directory_errors
async def add_vehicle(
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    request: CreateVehicleRequest,
) -> VehicleResponse:
    await require_confirmed_action(
        session,
        user=user,
        action="vehicle.create",
        payload=request.model_dump(
            mode="json", exclude={"confirmation_token"}, exclude_none=True, exclude_unset=True
        ),
        confirmation_token=request.confirmation_token,
    )
    return await vehicles.add_vehicle(request=request, user=user, session=session)


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleResponse)
@directory_errors
async def update_vehicle(
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    vehicle_id: uuid.UUID,
    request: UpdateVehicleRequest,
) -> VehicleResponse:
    await reads.ensure_exists(session, Vehicle, vehicle_id)
    confirmation_payload = request.model_dump(
        mode="json",
        exclude={"confirmation_token"},
        exclude_none=True,
        exclude_unset=True,
    )
    confirmation_payload["vehicle_id"] = str(vehicle_id)
    await require_confirmed_action(
        session,
        user=user,
        action="vehicle.update",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    return await vehicles.update_vehicle(
        vehicle_id=vehicle_id, request=request, user=user, session=session
    )


@router.post("/vehicles/{vehicle_id}/refresh-information", response_model=VehicleResponse)
@directory_errors
async def refresh_vehicle_information(
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    vehicle_id: uuid.UUID,
    request: Annotated[DirectoryConfirmationRequest | None, Body()] = None,
) -> VehicleResponse:
    await reads.ensure_exists(session, Vehicle, vehicle_id)
    await require_confirmed_action(
        session,
        user=user,
        action="vehicle.information_refresh",
        payload={"vehicle_id": str(vehicle_id)},
        confirmation_token=request.confirmation_token if request else None,
    )
    return await vehicles.refresh_vehicle_information(vehicle_id=vehicle_id, user=user, session=session)


@router.delete("/vehicles/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
@directory_errors
async def delete_vehicle(
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    vehicle_id: uuid.UUID,
    request: Annotated[DirectoryConfirmationRequest | None, Body()] = None,
) -> None:
    await reads.ensure_exists(session, Vehicle, vehicle_id)
    await require_confirmed_action(
        session,
        user=user,
        action="vehicle.delete",
        payload={"vehicle_id": str(vehicle_id)},
        confirmation_token=request.confirmation_token if request else None,
    )
    return await vehicles.delete_vehicle(vehicle_id=vehicle_id, user=user, session=session)


@router.post("/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
@directory_errors
async def add_group(
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    request: CreateGroupRequest,
) -> GroupResponse:
    await require_confirmed_action(
        session,
        user=user,
        action="group.create",
        payload=request.model_dump(
            mode="json", exclude={"confirmation_token"}, exclude_none=True, exclude_unset=True
        ),
        confirmation_token=request.confirmation_token,
    )
    return await groups.add_group(request=request, user=user, session=session)


@router.patch("/groups/{group_id}", response_model=GroupResponse)
@directory_errors
async def update_group(
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    group_id: uuid.UUID,
    request: UpdateGroupRequest,
) -> GroupResponse:
    await reads.ensure_exists(session, Group, group_id)
    confirmation_payload = request.model_dump(
        mode="json",
        exclude={"confirmation_token"},
        exclude_none=True,
        exclude_unset=True,
    )
    confirmation_payload["group_id"] = str(group_id)
    await require_confirmed_action(
        session,
        user=user,
        action="group.update",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    return await groups.update_group(group_id=group_id, request=request, user=user, session=session)


@router.get("/people/{person_id}/photo")
async def person_photo(
    _: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    person_id: uuid.UUID,
    variant: PhotoVariant = "full",
) -> Response:
    person = await session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    return await data_url_media_response(person.profile_photo_data_url, variant=variant)


@router.get("/vehicles/{vehicle_id}/photo")
async def vehicle_photo(
    _: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    vehicle_id: uuid.UUID,
    variant: PhotoVariant = "full",
) -> Response:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    return await data_url_media_response(vehicle.vehicle_photo_data_url, variant=variant)


@router.get("/groups", response_model=list[GroupResponse])
@directory_errors
async def list_groups(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[GroupResponse]:
    return await reads.list_groups(session)


@router.get("/people", response_model=DirectoryPage[PersonResponse])
@directory_errors
async def list_people(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    q: Annotated[str, Query(max_length=200)] = "",
    active: bool | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ids: Annotated[list[uuid.UUID] | None, Query(max_length=200)] = None,
    group_id: uuid.UUID | None = None,
    include_media: bool = False,
) -> DirectoryPage[PersonResponse]:
    return await reads.list_people(
        session,
        q=q,
        active=active,
        cursor=cursor,
        limit=limit,
        ids=ids,
        include_media=include_media,
        group_id=group_id,
    )


@router.get("/people/{item_id}", response_model=PersonResponse)
@directory_errors
async def get_person(
    item_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db_session)]
) -> PersonResponse:
    return await reads.get_person(session, item_id)


@router.get("/vehicles", response_model=DirectoryPage[VehicleResponse])
@directory_errors
async def list_vehicles(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    q: Annotated[str, Query(max_length=200)] = "",
    active: bool | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ids: Annotated[list[uuid.UUID] | None, Query(max_length=200)] = None,
    registrations: Annotated[list[str] | None, Query(max_length=200)] = None,
    include_media: bool = False,
) -> DirectoryPage[VehicleResponse]:
    return await reads.list_vehicles(
        session,
        q=q,
        active=active,
        cursor=cursor,
        limit=limit,
        ids=ids,
        registrations=registrations,
        include_media=include_media,
    )


@router.get("/vehicles/{item_id}", response_model=VehicleResponse)
@directory_errors
async def get_vehicle(
    item_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db_session)]
) -> VehicleResponse:
    return await reads.get_vehicle(session, item_id)


@router.get("/vehicles/{vehicle_id}/mot-history", response_model=MotHistoryPage)
@directory_errors
async def vehicle_mot_history(
    vehicle_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> MotHistoryPage:
    return await vehicles.mot_history(session, vehicle_id, cursor=cursor, limit=limit)
