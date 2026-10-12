-- A resident's UPI ID, so a settlement summary can carry a payable link.
-- Optional, and never used for accounting: it only decorates what is shared.
ALTER TABLE resident ADD COLUMN upi_id TEXT;
