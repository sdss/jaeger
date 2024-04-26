CREATE SCHEMA opsdb_apo;

SET search_path TO opsdb_apo;

CREATE TABLE opsdb_apo.configuration (
    configuration_id SERIAL PRIMARY KEY NOT NULL,
    design_id INTEGER,
    comment TEXT,
    temperature TEXT,
    epoch DOUBLE PRECISION,
    calibration_version TEXT);

CREATE TABLE opsdb_apo.assignment_to_focal (
    pk SERIAL PRIMARY KEY NOT NULL,
    assignment_pk INTEGER,
    configuration_id INTEGER,
    xfocal REAL,
    yfocal REAL,
    positioner_id SMALLINT,
    catalogid BIGINT,
    collided BOOLEAN,
    replaced BOOLEAN);

CREATE TABLE opsdb_apo.completion_status (
    pk SERIAL PRIMARY KEY NOT NULL,
    label TEXT);

CREATE TABLE opsdb_apo.design_to_status (
    pk SERIAL PRIMARY KEY NOT NULL,
    design_id INTEGER UNIQUE,
    completion_status_pk SMALLINT,
    mjd REAL,
    manual BOOL DEFAULT false);

CREATE TABLE opsdb_apo.exposure (
    pk SERIAL PRIMARY KEY NOT NULL,
    configuration_id INTEGER,
    survey_pk SMALLINT,
    exposure_no BIGINT,
    comment TEXT,
    start_time TIMESTAMP,
    exposure_time REAL,
    -- exposure_status_pk SMALLINT,
    exposure_flavor_pk SMALLINT NOT NULL);
    -- camera_pk SMALLINT);

CREATE TABLE opsdb_apo.survey (
    pk SERIAL PRIMARY KEY NOT NULL,
    label TEXT);

CREATE TABLE opsdb_apo.camera (
    pk SERIAL PRIMARY KEY NOT NULL,
    instrument_pk SMALLINT,
    label TEXT);

CREATE TABLE opsdb_apo.exposure_flavor (
    pk SERIAL PRIMARY KEY NOT NULL,
    label TEXT);

CREATE TABLE opsdb_apo.camera_frame (
    pk SERIAL PRIMARY KEY NOT NULL,
    exposure_pk INTEGER NOT NULL,
    camera_pk SMALLINT NOT NULL,
    ql_sn2 REAL,
    sn2 REAL,
    comment TEXT);

CREATE TABLE opsdb_apo.queue(
    pk SERIAL PRIMARY KEY NOT NULL,
    design_id INTEGER,
    position SMALLINT,
    mjd_plan DOUBLE PRECISION);

CREATE TABLE opsdb_apo.field_priority(
    pk SERIAL PRIMARY KEY NOT NULL,
    label TEXT);

CREATE TABLE opsdb_apo.field_to_priority(
    pk SERIAL PRIMARY KEY NOT NULL,
    field_pk INTEGER,
    field_priority_pk INTEGER);

CREATE TABLE opsdb_apo.quicklook(
    pk SERIAL PRIMARY KEY NOT NULL,
    snr_standard REAL,
    logsnr_hmag_coef REAL[],
    exposure_pk INTEGER,
    readnum INTEGER,
    exptype TEXT,
    hmag_standard REAL,
    snr_standard_scale REAL,
    snr_predict REAL,
    logsnr_hmag_coef_all REAL[],
    zeropt REAL);

CREATE TABLE opsdb_apo.quickred(
    pk SERIAL PRIMARY KEY NOT NULL,
    exposure_pk INTEGER,
    snr_standard REAL,
    logsnr_hmag_coef REAL[],
    dither_pixpos REAL,
    snr_source TEXT,
    hmag_standard REAL,
    snr_standard_scale REAL,
    logsnr_hmag_coef_all REAL[],
    zeropt REAL,
    dither_named TEXT);

CREATE TABLE opsdb_apo.base_priority(
    pk SERIAL PRIMARY KEY NOT NULL,
    field_pk INTEGER,
    priority INTEGER,
    version_pk INTEGER);

CREATE TABLE opsdb_apo.priority_version(
    pk SERIAL PRIMARY KEY NOT NULL,
    label TEXT);

CREATE TABLE opsdb_apo.overhead(
    pk SERIAL PRIMARY KEY NOT NULL,
    configuration_id INTEGER,
    macro_id INTEGER,
    macro TEXT,
    stage TEXT,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    elapsed REAL,
    success BOOLEAN);

-- Foreign keys

ALTER TABLE ONLY opsdb_apo.base_priority
    ADD CONSTRAINT field_fk
    FOREIGN KEY (field_pk) REFERENCES targetdb.field(pk)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.base_priority
    ADD CONSTRAINT pri_version_fk
    FOREIGN KEY (version_pk) REFERENCES opsdb_apo.priority_version(pk)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.field_to_priority
    ADD CONSTRAINT field_fk
    FOREIGN KEY (field_pk) REFERENCES targetdb.field(pk)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.field_to_priority
    ADD CONSTRAINT field_priority_fk
    FOREIGN KEY (field_priority_pk) REFERENCES opsdb_apo.field_priority(pk)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.queue
    ADD CONSTRAINT queue_design_fk
    FOREIGN KEY (design_id) REFERENCES targetdb.design(design_id);

ALTER TABLE ONLY opsdb_apo.configuration
    ADD CONSTRAINT config_design_fk
    FOREIGN KEY (design_id) REFERENCES targetdb.design(design_id);

ALTER TABLE ONLY opsdb_apo.assignment_to_focal
    ADD CONSTRAINT configuration_fk
    FOREIGN KEY (configuration_id) REFERENCES opsdb_apo.configuration(configuration_id)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.assignment_to_focal
    ADD CONSTRAINT assignment_fk
    FOREIGN KEY (assignment_pk) REFERENCES targetdb.assignment(pk)
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.design_to_status
    ADD CONSTRAINT status_design_fk
    FOREIGN KEY (design_id) REFERENCES targetdb.design(design_id)
    ON UPDATE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.design_to_status
    ADD CONSTRAINT completion_status_fk
    FOREIGN KEY (completion_status_pk) REFERENCES opsdb_apo.completion_status(pk)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.exposure
    ADD CONSTRAINT configuration_fk
    FOREIGN KEY (configuration_id) REFERENCES opsdb_apo.configuration(configuration_id);

ALTER TABLE ONLY opsdb_apo.exposure
    ADD CONSTRAINT survey_fk
    FOREIGN KEY (survey_pk) REFERENCES opsdb_apo.survey(pk);

ALTER TABLE ONLY opsdb_apo.exposure
    ADD CONSTRAINT exposure_flavor_fk
    FOREIGN KEY (exposure_flavor_pk) REFERENCES opsdb_apo.exposure_flavor(pk);

ALTER TABLE ONLY opsdb_apo.camera_frame
    ADD CONSTRAINT camera_fk
    FOREIGN KEY (camera_pk) REFERENCES opsdb_apo.camera(pk);

ALTER TABLE ONLY opsdb_apo.camera_frame
    ADD CONSTRAINT exposure_fk
    FOREIGN KEY (exposure_pk) REFERENCES opsdb_apo.exposure(pk)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.camera
    ADD CONSTRAINT instrument_fk
    FOREIGN KEY (instrument_pk) REFERENCES targetdb.instrument(pk);

ALTER TABLE ONLY opsdb_apo.quicklook
    ADD CONSTRAINT ql_exposure_fk
    FOREIGN KEY (exposure_pk) REFERENCES opsdb_apo.exposure(pk)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.quickred
    ADD CONSTRAINT qr_exposure_fk
    FOREIGN KEY (exposure_pk) REFERENCES opsdb_apo.exposure(pk)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY opsdb_apo.overhead
    ADD CONSTRAINT configuration_id_fk
    FOREIGN KEY (configuration_id) REFERENCES opsdb_apo.configuration(configuration_id)
    ON UPDATE CASCADE ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

-- Table data

INSERT INTO opsdb_apo.exposure_flavor VALUES
    (1, 'Science'), (2, 'Arc'), (3, 'Flat'), (4, 'Bias'),
    (5, 'Object'), (6, 'Dark'), (7, 'Sky'), (8, 'Calib'),
    (9, 'LocalFlat'), (10, 'SuperDark'), (11, 'SuperFlat'),
    (12, 'DomeFlat'), (13, 'QuartzFlat'), (14, 'ArcLamp');

INSERT INTO opsdb_apo.survey VALUES (1, 'BHM'), (2, 'MWM');

-- BOSS instrument defined to be 0 in targetdb.sql and APOGEE is 1

INSERT INTO opsdb_apo.camera VALUES (1, 0, 'r1'), (2, 0, 'b1'), (3, 1, 'APOGEE');

INSERT INTO opsdb_apo.completion_status VALUES (1, 'not started'), (2, 'started'), (3, 'done');

INSERT INTO opsdb_apo.field_priority VALUES (0, 'disabled'), (1, 'top');

-- Indices

CREATE INDEX CONCURRENTLY design_id_idx
    ON opsdb_apo.configuration
    USING BTREE(design_id);

CREATE INDEX CONCURRENTLY assignment_pk_idx
    ON opsdb_apo.assignment_to_focal
    USING BTREE(assignment_pk);

CREATE INDEX CONCURRENTLY design_to_status_design_id_idx
    ON opsdb_apo.design_to_status
    USING BTREE(design_id);

CREATE INDEX CONCURRENTLY configuration_id_idx
    ON opsdb_apo.exposure
    USING BTREE(configuration_id);

CREATE INDEX CONCURRENTLY start_time_idx
    ON opsdb_apo.exposure
    USING BTREE(start_time);

CREATE INDEX CONCURRENTLY exposure_pk_idx
    ON opsdb_apo.camera_frame
    USING BTREE(exposure_pk);

CREATE INDEX CONCURRENTLY ql_exposure_pk_idx
    ON opsdb_apo.quicklook
    USING BTREE(exposure_pk);

CREATE INDEX CONCURRENTLY qr_exposure_pk_idx
    ON opsdb_apo.quickred
    USING BTREE(exposure_pk);

CREATE INDEX CONCURRENTLY overhead_configuration_id_idx
    ON opsdb_apo.overhead
    USING BTREE(configuration_id);

-- pop function to retrieve next in queue and increment

CREATE FUNCTION opsdb_apo.popQueue ()
RETURNS integer AS $design$

declare
    design integer;
    _pk integer;
    _design integer;
    _pos integer;

BEGIN
    FOR _pk, _design, _pos IN
        SELECT * FROM opsdb_apo.queue
        ORDER BY position
    LOOP
        IF _pos = 1 then
            design := _design;
            UPDATE opsdb_apo.queue SET position = -1 WHERE pk=_pk;
        ELSE
            UPDATE opsdb_apo.queue SET position = _pos - 1 WHERE pk=_pk;
        END IF;
    END LOOP;
    RETURN design;
END;
$design$ LANGUAGE plpgsql;

-- add to end of queue

CREATE FUNCTION opsdb_apo.appendQueue (design integer, mjd real)
RETURNS void AS $$

declare
    maxpos integer;

BEGIN
    SELECT MAX(position) INTO maxpos FROM opsdb_apo.queue;
    IF maxpos IS NULL THEN SELECT 0 INTO maxpos; END IF;
    INSERT INTO opsdb_apo.queue  (design_id, position, mjd_plan)
    VALUES (design, maxpos+1, mjd);
END;
$$ LANGUAGE plpgsql;

-- insert at position

CREATE FUNCTION opsdb_apo.insertInQueue (design integer, pos integer, exp_len real, mjd real)
RETURNS void AS $$

declare
    _pk integer;
    _design integer;
    _pos integer;
    _mjd_plan real;
    _mjd_offset real;
    _mjd_next real;

BEGIN
    IF exp_len IS NULL THEN
        SELECT 0 INTO _mjd_offset;
    ELSE
        SELECT exp_len INTO _mjd_offset;
    END IF;
    FOR _pk, _design, _pos, _mjd_plan IN
        SELECT * FROM opsdb_apo.queue
        WHERE position >= pos
    LOOP
        IF _pos = pos THEN
            SELECT _mjd_plan INTO _mjd_next;
        END IF;
        UPDATE opsdb_apo.queue SET position = _pos + 1 WHERE pk=_pk;
        UPDATE opsdb_apo.queue SET mjd_plan = _mjd_plan + _mjd_offset WHERE pk=_pk;
    END LOOP;

    IF _mjd_next IS NULL THEN
        SELECT mjd INTO _mjd_next;
    END IF;

    INSERT INTO opsdb_apo.queue  (design_id, position, mjd_plan)
    VALUES (design, pos, _mjd_next);
END;
$$ LANGUAGE plpgsql;

