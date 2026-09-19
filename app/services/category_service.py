from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.repositories.category_repository import CategoryRepository
from app.schemas.category import CategoryCreate, CategoryUpdate


class CategoryService:
    def __init__(self, db: Session):
        self.repo = CategoryRepository(db)

    def get_all(self):
        return self.repo.get_all()

    def get_by_id(self, category_id: int):
        cat = self.repo.get_by_id(category_id)
        if not cat:
            raise HTTPException(status_code=404, detail="Category not found")
        return cat

    def create(self, data: CategoryCreate):
        return self.repo.create(data)

    def update(self, category_id: int, data: CategoryUpdate):
        cat = self.repo.update(category_id, data)
        if not cat:
            raise HTTPException(status_code=404, detail="Category not found")
        return cat

    def delete(self, category_id: int):
        if not self.repo.delete(category_id):
            raise HTTPException(status_code=404, detail="Category not found")
