from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. Составной индекс под «мои бронирования» -------------------
    # DESC-порядок колонки задаётся сырым SQL: op.create_index() не умеет
    # описывать направление сортировки внутри ключа индекса.
    op.execute(
        "CREATE INDEX ix_bookings_user_id_created_at "
        "ON bookings (user_id, created_at DESC)"
    )

    # --- 2. Составной индекс под список бронирований с фильтрами ------
    op.execute(
        "CREATE INDEX ix_bookings_status_created_at "
        "ON bookings (status, created_at DESC)"
    )

    # --- 3-5. Удаление избыточных индексов ----------------------------
    op.drop_index("ix_bookings_id", table_name="bookings")
    op.drop_index("ix_bookings_user_id", table_name="bookings")
    op.drop_index("ix_bookings_status", table_name="bookings")


def downgrade() -> None:
    op.create_index("ix_bookings_status", "bookings", ["status"], unique=False)
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"], unique=False)
    op.create_index("ix_bookings_id", "bookings", ["id"], unique=False)

    op.drop_index("ix_bookings_status_created_at", table_name="bookings")
    op.drop_index("ix_bookings_user_id_created_at", table_name="bookings")
