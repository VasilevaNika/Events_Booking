from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.category_service import CategoryService
from app.schemas.category import CategoryCreate, CategoryUpdate

router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("")
def get_categories(db: Session = Depends(get_db)):
    return CategoryService(db).get_all()


@router.get("/{category_id}")
def get_category(category_id: int, db: Session = Depends(get_db)):
    return CategoryService(db).get_by_id(category_id)


@router.post("", status_code=201)
def create_category(data: CategoryCreate, db: Session = Depends(get_db)):
    return CategoryService(db).create(data)


@router.put("/{category_id}")
def update_category(category_id: int, data: CategoryUpdate, db: Session = Depends(get_db)):
    return CategoryService(db).update(category_id, data)


@router.delete("/{category_id}", status_code=204)
def delete_category(category_id: int, db: Session = Depends(get_db)):
    CategoryService(db).delete(category_id)
