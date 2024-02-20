\COPY targetdb.cadence TO 'sample_files/cadence.csv' WITH CSV HEADER;
\COPY targetdb.carton TO 'sample_files/carton.csv' WITH CSV HEADER;
\COPY targetdb.design_mode TO 'sample_files/design_mode.csv' WITH CSV HEADER;
\COPY targetdb.hole TO 'sample_files/hole.csv' WITH CSV HEADER;
\COPY targetdb.obsmode TO 'sample_files/obsmode.csv' WITH CSV HEADER;
\COPY targetdb.positioner_status TO 'sample_files/positioner_status.csv' WITH CSV HEADER;
\COPY targetdb.version TO 'sample_files/version.csv' WITH CSV HEADER;

CREATE TEMP TABLE temp_assignment AS (
    SELECT a.* from targetdb.assignment a
       JOIN design d ON a.design_id = d.design_id
       WHERE d.design_id IN (505253, 505254, 503246, 503247, 502706, 21636, 21637));
\COPY temp_assignment TO 'sample_files/assignment.csv' WITH CSV HEADER;

CREATE TEMP TABLE temp_assignment_status AS (
    SELECT ast.* from targetdb.assignment_status ast
       JOIN temp_assignment ta ON ast.assignment_pk = ta.pk);
\COPY temp_assignment_status TO 'sample_files/assignment_status.csv' WITH CSV HEADER;

CREATE TEMP TABLE temp_carton_to_target AS (
    SELECT c.* from targetdb.carton_to_target c
       JOIN temp_assignment ta ON ta.carton_to_target_pk = c.pk);
\COPY temp_carton_to_target TO 'sample_files/carton_to_target.csv' WITH CSV HEADER;

CREATE TEMP TABLE temp_target AS (
    SELECT t.* from targetdb.target t
       JOIN temp_carton_to_target ctt ON t.pk = ctt.target_pk);
\COPY temp_target TO 'sample_files/target.csv' WITH CSV HEADER;

CREATE TEMP TABLE temp_design AS (
    SELECT DISTINCT ON (d.design_id) d.* from targetdb.design d
       JOIN temp_assignment t ON d.design_id = t.design_id);
\COPY temp_design TO 'sample_files/design.csv' WITH CSV HEADER;

CREATE TEMP TABLE temp_design_to_field AS (
    SELECT DISTINCT ON (d.design_id) d.* from targetdb.design_to_field d
       JOIN temp_design t ON d.design_id = t.design_id);
\COPY temp_design_to_field TO 'sample_files/design_to_field.csv' WITH CSV HEADER;

CREATE TEMP TABLE temp_field AS (
    SELECT f.* from targetdb.field f
       JOIN temp_design_to_field d ON f.pk = d.field_pk);
\COPY temp_field TO 'sample_files/field.csv' WITH CSV HEADER;

CREATE TEMP TABLE temp_magnitude AS (
    SELECT m.* from targetdb.magnitude m
       JOIN temp_carton_to_target tctt ON tctt.pk = m.carton_to_target_pk);
\COPY temp_magnitude TO 'sample_files/magnitude.csv' WITH CSV HEADER;
