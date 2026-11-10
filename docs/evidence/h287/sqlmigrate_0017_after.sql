BEGIN;
--
-- Add field is_active to session
--
ALTER TABLE "classrooms_session" ADD COLUMN "is_active" boolean DEFAULT false NOT NULL;
ALTER TABLE "classrooms_session" ALTER COLUMN "is_active" DROP DEFAULT;
--
-- Create constraint one_active_session_per_school on model session
--
CREATE UNIQUE INDEX "one_active_session_per_school" ON "classrooms_session" ("school_id") WHERE ("is_active" AND "owner_type" = 'SCHOOL');
--
-- Create constraint one_active_session_per_teacher on model session
--
CREATE UNIQUE INDEX "one_active_session_per_teacher" ON "classrooms_session" ("teacher_id") WHERE ("is_active" AND "owner_type" = 'INDIVIDUAL');
COMMIT;
