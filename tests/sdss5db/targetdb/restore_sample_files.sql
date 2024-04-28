\COPY targetdb.version FROM 'sample_files/version.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.category FROM 'sample_files/category.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.cadence FROM 'sample_files/cadence.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.positioner_status FROM 'sample_files/positioner_status.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.carton FROM 'sample_files/carton.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.design_mode FROM 'sample_files/design_mode.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.hole FROM 'sample_files/hole.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.obsmode FROM 'sample_files/obsmode.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.target FROM 'sample_files/target.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.carton_to_target FROM 'sample_files/carton_to_target.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.design FROM 'sample_files/design.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.assignment FROM 'sample_files/assignment.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.assignment_status FROM 'sample_files/assignment_status.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.field FROM 'sample_files/field.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.design_to_field FROM 'sample_files/design_to_field.csv' WITH DELIMITER ',' CSV HEADER;
\COPY targetdb.magnitude FROM 'sample_files/magnitude.csv' WITH DELIMITER ',' CSV HEADER;

-- Delete one assignment from design 21636 to test cases when not all 500 robots are
-- assigned a target.
DELETE FROM targetdb.assignment_status WHERE assignment_pk = 9736993;
DELETE FROM targetdb.assignment WHERE pk = 9736993;
