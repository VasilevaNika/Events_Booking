from sqlalchemy.orm import Session
from typing import Optional
from fastapi import HTTPException
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate, UserUpdate


class UserService:
    def __init__(self, db: Session):
        self.repo = UserRepository(db)

    def get_all(self, page: int = 1, page_size: int = 20, search: Optional[str] = None) -> dict:
        skip = (page - 1) * page_size
        items = self.repo.get_all(skip=skip, limit=page_size, search=search)
        total = self.repo.count(search=search)
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}

    def get_by_id(self, user_id: int):
        user = self.repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def create(self, data: UserCreate):
        if self.repo.get_by_email(data.email):
            raise HTTPException(status_code=400, detail="Email already registered")
        return self.repo.create(data)

    def update(self, user_id: int, data: UserUpdate):
        user = self.repo.update(user_id, data)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def delete(self, user_id: int):
        if not self.repo.delete(user_id):
            raise HTTPException(status_code=404, detail="User not found")

    def get_stats(self, user_id: int) -> dict:
        stats = self.repo.get_user_stats(user_id)
        if not stats:
            raise HTTPException(status_code=404, detail="User not found")
        return stats
