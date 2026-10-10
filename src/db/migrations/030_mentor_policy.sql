-- Automatic checks remain replayable without reserving completed coaching.
ALTER TABLE generation_run ADD COLUMN mentor_trigger VARCHAR(10)
    CHECK (mentor_trigger IN ('manual','auto'));
DROP INDEX uq_run_completed_turn;
CREATE UNIQUE INDEX uq_run_completed_turn ON generation_run(session_id,turn_id,operation)
    WHERE status = 'completed' AND (operation = 'student' OR result_kind = 'message');
