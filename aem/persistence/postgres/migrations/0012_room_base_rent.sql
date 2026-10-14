-- What each room's occupant pays towards the rent, and one deposit slot per room.
--
-- A room's base rent lets the month's rent be split by room rather than
-- equally: a room with its own washroom or a balcony can carry more of it.
-- It is BIGINT paise, and null until set. A room is backed by at most one
-- deposit slot, and a slot by at most one room.

ALTER TABLE household_room
    ADD COLUMN base_rent_paise BIGINT CHECK (base_rent_paise IS NULL OR base_rent_paise >= 0);

CREATE UNIQUE INDEX idx_deposit_slot_unique_room
    ON household_deposit_slot(apartment_id, room_id)
    WHERE room_id IS NOT NULL;
