-- Схема шарда (лабораторная №5).
--
-- Выполняется один раз, при первом старте контейнера шарда
-- (docker-entrypoint-initdb.d). Все шарды одинаковые по структуре —
-- различаются только данными: каждый хранит бронирования тех пользователей,
-- которых router отнёс к нему.
--
-- Справочники (users, events, venues) остаются на primary, поэтому внешних
-- ключей здесь нет: PostgreSQL не умеет ссылаться на таблицу другого сервера.
-- Целостность ссылок проверяет приложение перед записью.

CREATE TABLE bookings (
    id          BIGINT           PRIMARY KEY,   -- глобальный id: выдаёт последовательность на primary
    user_id     INTEGER          NOT NULL,      -- shard key
    event_id    INTEGER          NOT NULL,
    status      VARCHAR(20)      NOT NULL,
    seats_count INTEGER          NOT NULL,
    total_price DOUBLE PRECISION NOT NULL,
    created_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ
);

-- Главный запрос шарда — «бронирования пользователя, свежие сверху».
-- Тот же составной индекс, что на primary (лабораторная №1).
CREATE INDEX ix_bookings_user_id_created_at ON bookings (user_id, created_at DESC);

-- «Последние бронирования всей системы» (лабораторная №6): каждый шард отдаёт
-- свой топ по времени. Без индекса шард сортировал бы все строки на каждый
-- запрос. id — для однозначного порядка и keyset-пагинации.
CREATE INDEX ix_bookings_created_at_id ON bookings (created_at DESC, id DESC);
