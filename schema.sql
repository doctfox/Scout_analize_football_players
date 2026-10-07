-- =====================================================================
-- Схема БД скаутского приложения (PostgreSQL + pgvector)
-- Справочно: таблицы создаёт сам парсер (python main.py init) по models.py.
-- Денежные величины — в млн €. Сезон — строка '2023/24'.
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS football;

CREATE TABLE IF NOT EXISTS football.league_seasons (
	id SERIAL NOT NULL, 
	league VARCHAR(100) NOT NULL, 
	season VARCHAR(10) NOT NULL, 
	avg_team_value FLOAT,           -- средняя стоимость состава команды, млн €
	strength_coefficient FLOAT, 
	PRIMARY KEY (id), 
	UNIQUE (league, season)
);

CREATE TABLE IF NOT EXISTS football.teams (
	team_id SERIAL NOT NULL, 
	tm_club_id VARCHAR(20) NOT NULL, 
	team_name VARCHAR(150) NOT NULL, 
	league VARCHAR(100), 
	PRIMARY KEY (team_id), 
	UNIQUE (tm_club_id)
);

CREATE TABLE IF NOT EXISTS football.players (
	player_id SERIAL NOT NULL, 
	tm_player_id VARCHAR(20) NOT NULL, 
	understat_id INTEGER, 
	whoscored_id INTEGER, 
	sofifa_id INTEGER, 
	player_name VARCHAR(150) NOT NULL, 
	birth_date DATE, 
	position VARCHAR(50), 
	nationality VARCHAR(100), 
	foot VARCHAR(20), 
	height_cm INTEGER, 
	current_market_value FLOAT, 
	team_id INTEGER, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (player_id), 
	UNIQUE (tm_player_id), 
	UNIQUE (understat_id), 
	UNIQUE (whoscored_id), 
	UNIQUE (sofifa_id), 
	FOREIGN KEY(team_id) REFERENCES football.teams (team_id)
);

CREATE TABLE IF NOT EXISTS football.player_stats (
	stat_id SERIAL NOT NULL, 
	player_id INTEGER NOT NULL, 
	team_id INTEGER NOT NULL, 
	league VARCHAR(100) NOT NULL, 
	season VARCHAR(10) NOT NULL, 
	age_at_season INTEGER, 
	appearances INTEGER, 
	matches_started INTEGER, 
	matches_subbed INTEGER, 
	minutes_played INTEGER, 
	goals INTEGER, 
	assists INTEGER, 
	yellow_cards INTEGER, 
	red_cards INTEGER, 
	penalties_scored INTEGER, 
	penalties_attempted INTEGER, 
	max_speed FLOAT,                 -- км/ч, реальный замер (bundesliga.com), иначе NULL
	max_speed_source VARCHAR(50), 
	max_speed_est FLOAT,             -- км/ч, оценка по рейтингам EA FC (шаг speed_model)
	xg FLOAT, 
	npxg FLOAT, 
	xa FLOAT, 
	xg_chain FLOAT, 
	xg_buildup FLOAT, 
	team_possession_share FLOAT, 
	league_strength_coefficient FLOAT, 
	data_sources VARCHAR(100), 
	feature_vector VECTOR(22),       -- признаки: config.VECTOR_FEATURES
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	goals_per_match FLOAT, 
	assists_per_match FLOAT, 
	xg_per_match FLOAT, 
	xa_per_match FLOAT, 
	shots_per_match FLOAT, 
	shots_on_target_per_match FLOAT, 
	key_passes_per_match FLOAT, 
	passes_per_match FLOAT, 
	passes_completed_per_match FLOAT, 
	forward_passes_per_match FLOAT, 
	forward_passes_completed_per_match FLOAT, 
	progressive_passes_per_match FLOAT, 
	crosses_per_match FLOAT, 
	long_balls_per_match FLOAT, 
	dribbles_per_match FLOAT, 
	dribbles_won_per_match FLOAT, 
	tackles_per_match FLOAT, 
	tackles_won_per_match FLOAT, 
	interceptions_per_match FLOAT, 
	clearances_per_match FLOAT, 
	blocks_per_match FLOAT, 
	aerials_per_match FLOAT, 
	aerials_won_per_match FLOAT, 
	ball_recoveries_per_match FLOAT, 
	fouls_per_match FLOAT, 
	fouled_per_match FLOAT, 
	dispossessed_per_match FLOAT, 
	miscontrols_per_match FLOAT, 
	touches_per_match FLOAT, 
	shots_p90 FLOAT, 
	shots_on_target_p90 FLOAT, 
	key_passes_p90 FLOAT, 
	passes_p90 FLOAT, 
	forward_passes_p90 FLOAT, 
	progressive_passes_p90 FLOAT, 
	dribbles_p90 FLOAT, 
	tackles_p90 FLOAT, 
	interceptions_p90 FLOAT, 
	blocks_p90 FLOAT, 
	clearances_p90 FLOAT, 
	aerials_won_p90 FLOAT, 
	dispossessed_p90 FLOAT, 
	miscontrols_p90 FLOAT, 
	xg_p90 FLOAT, 
	npxg_p90 FLOAT, 
	xa_p90 FLOAT, 
	xg_chain_p90 FLOAT, 
	xg_buildup_p90 FLOAT, 
	pass_completion_pct FLOAT, 
	forward_pass_completion_pct FLOAT, 
	dribble_success_pct FLOAT, 
	tackles_won_pct FLOAT, 
	aerials_won_pct FLOAT, 
	duels_won_pct FLOAT, 
	PRIMARY KEY (stat_id), 
	UNIQUE (player_id, team_id, league, season), 
	FOREIGN KEY(player_id) REFERENCES football.players (player_id), 
	FOREIGN KEY(team_id) REFERENCES football.teams (team_id)
);

CREATE TABLE IF NOT EXISTS football.squads (
	squad_id SERIAL NOT NULL, 
	team_id INTEGER NOT NULL, 
	player_id INTEGER NOT NULL, 
	league VARCHAR(100) NOT NULL, 
	season VARCHAR(10) NOT NULL, 
	market_value FLOAT, 
	PRIMARY KEY (squad_id), 
	UNIQUE (team_id, player_id, season), 
	FOREIGN KEY(team_id) REFERENCES football.teams (team_id), 
	FOREIGN KEY(player_id) REFERENCES football.players (player_id)
);

CREATE TABLE IF NOT EXISTS football.transfers (
	transfer_id SERIAL NOT NULL, 
	tm_transfer_id VARCHAR(30) NOT NULL, 
	player_id INTEGER NOT NULL, 
	team_out_id INTEGER, 
	team_in_id INTEGER, 
	transfer_date DATE, 
	year INTEGER, 
	season VARCHAR(10), 
	transfer_fee FLOAT, 
	market_value_at_transfer FLOAT, 
	is_upcoming BOOLEAN, 
	PRIMARY KEY (transfer_id), 
	UNIQUE (player_id, tm_transfer_id), 
	FOREIGN KEY(player_id) REFERENCES football.players (player_id), 
	FOREIGN KEY(team_out_id) REFERENCES football.teams (team_id), 
	FOREIGN KEY(team_in_id) REFERENCES football.teams (team_id)
);

-- Рейтинги EA FC (sofifa.com), 0–99: экспертные оценки, не замеры
CREATE TABLE IF NOT EXISTS football.player_ratings (
	rating_id SERIAL NOT NULL, 
	player_id INTEGER NOT NULL, 
	season VARCHAR(10) NOT NULL, 
	sofifa_id INTEGER NOT NULL, 
	fifa_edition VARCHAR(20), 
	version_id INTEGER, 
	pace INTEGER,                     -- 0.45*acceleration + 0.55*sprint_speed
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	overall_rating INTEGER, 
	potential INTEGER, 
	acceleration INTEGER, 
	sprint_speed INTEGER, 
	agility INTEGER, 
	balance INTEGER, 
	stamina INTEGER, 
	strength INTEGER, 
	jumping INTEGER, 
	reactions INTEGER, 
	dribbling INTEGER, 
	ball_control INTEGER, 
	short_passing INTEGER, 
	long_passing INTEGER, 
	vision INTEGER, 
	crossing INTEGER, 
	finishing INTEGER, 
	shot_power INTEGER, 
	long_shots INTEGER, 
	positioning INTEGER, 
	interceptions INTEGER, 
	defensive_awareness INTEGER, 
	standing_tackle INTEGER, 
	sliding_tackle INTEGER, 
	aggression INTEGER, 
	heading_accuracy INTEGER, 
	composure INTEGER, 
	PRIMARY KEY (rating_id), 
	UNIQUE (player_id, season), 
	FOREIGN KEY(player_id) REFERENCES football.players (player_id)
);

-- Журнал калибровочной модели 'рейтинги EA FC -> макс. скорость'
CREATE TABLE IF NOT EXISTS football.speed_models (
	model_id SERIAL NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	algorithm VARCHAR(50), 
	features TEXT, 
	coefficients TEXT, 
	intercept FLOAT, 
	n_train INTEGER, 
	r2_cv FLOAT, 
	mae_cv FLOAT, 
	rmse_cv FLOAT, 
	n_predicted INTEGER, 
	PRIMARY KEY (model_id)
);


-- Индекс для быстрого поиска похожих игроков (косинусное расстояние)
CREATE INDEX IF NOT EXISTS player_stats_vec_idx
    ON football.player_stats USING hnsw (feature_vector vector_cosine_ops);

-- Пример: 10 игроков, наиболее похожих на игрока со stat_id = 1
-- SELECT p.player_name, ps.season, ps.league,
--        1 - (ps.feature_vector <=> ref.feature_vector) AS similarity
-- FROM football.player_stats ps
-- JOIN football.players p USING (player_id),
--      (SELECT feature_vector FROM football.player_stats WHERE stat_id = 1) ref
-- WHERE ps.feature_vector IS NOT NULL AND ps.stat_id <> 1
-- ORDER BY ps.feature_vector <=> ref.feature_vector
-- LIMIT 10;
