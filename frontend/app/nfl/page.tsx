"use client";

import { useEffect, useState } from "react";
import {
  fetchGameContext,
  fetchGames,
  type Game,
  type GameContext,
} from "@/lib/api";

const DATA_UNAVAILABLE = "DATA UNAVAILABLE";

/**
 * NFL hub: this week's slate with real injury reports and kickoff-hour
 * weather per game. Every section is honest about what exists:
 * - no backend / no database  -> DATA UNAVAILABLE (never a guessed slate)
 * - game has no injury rows   -> injuries DATA UNAVAILABLE (never "no injuries")
 * - game has no weather row   -> weather DATA UNAVAILABLE (never invented temps)
 *
 * Injuries are the official weekly NFL injury reports (nflverse release),
 * not a live news wire; weather is the kickoff-hour Open-Meteo record at
 * the venue, null fields stay null (dome games have no outdoor conditions).
 */
export default function NflPage() {
  const [season, setSeason] = useState(2026);
  const [week, setWeek] = useState(1);
  const [games, setGames] = useState<Game[] | null>(null);
  const [gamesStatus, setGamesStatus] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [contexts, setContexts] = useState<Record<string, GameContext | null>>({});

  useEffect(() => {
    let cancelled = false;
    setGames(null);
    setGamesStatus(null);
    setExpanded(null);
    fetchGames(season, week).then((result) => {
      if (cancelled) return;
      setGamesStatus(result === null ? "unreachable" : result.status);
      setGames(result?.games ?? []);
    });
    return () => {
      cancelled = true;
    };
  }, [season, week]);

  const toggle = (gameId: string) => {
    if (expanded === gameId) {
      setExpanded(null);
      return;
    }
    setExpanded(gameId);
    if (!(gameId in contexts)) {
      fetchGameContext(gameId).then((ctx) => {
        setContexts((prev) => ({ ...prev, [gameId]: ctx }));
      });
    }
  };

  return (
    <main className="container">
      <h1>NFL</h1>
      <p className="lede">
        This week&apos;s slate, with the official weekly injury reports and
        kickoff-hour venue weather for every game.
      </p>

      <div className="nfl-filters">
        <label>
          Season{" "}
          <input
            type="number"
            value={season}
            min={2016}
            max={2026}
            onChange={(e) => setSeason(Number(e.target.value))}
          />
        </label>
        <label>
          Week{" "}
          <select value={week} onChange={(e) => setWeek(Number(e.target.value))}>
            {Array.from({ length: 22 }, (_, i) => i + 1).map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
      </div>

      {games === null && (
        <p className="empty-state">Loading games&hellip;</p>
      )}
      {games !== null && games.length === 0 && (
        <p className="empty-state">
          <strong>{DATA_UNAVAILABLE}</strong>
          <br />
          {gamesStatus === "unreachable"
            ? "The API could not be reached from this browser."
            : "No games stored yet — ingestion has not loaded this season/week."}
        </p>
      )}
      {games !== null && games.length > 0 && (
        <ul className="game-list">
          {games.map((game) => (
            <li key={game.game_id} className="game-card">
              <button
                type="button"
                className="game-toggle"
                onClick={() => toggle(game.game_id)}
                aria-expanded={expanded === game.game_id}
              >
                <span className="game-teams">
                  {game.away_team} @ {game.home_team}
                </span>
                <span className="game-meta">
                  {game.game_date ?? ""} · {game.venue ?? "venue unknown"}
                </span>
                <span className="game-chevron">
                  {expanded === game.game_id ? "▾" : "▸"}
                </span>
              </button>
              {expanded === game.game_id && (
                <GameContextView context={contexts[game.game_id]} />
              )}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

function GameContextView({
  context,
}: {
  context: GameContext | null | undefined;
}) {
  if (context === undefined) {
    return <p className="empty-state">Loading game context&hellip;</p>;
  }
  if (context === null) {
    return (
      <p className="empty-state">
        <strong>{DATA_UNAVAILABLE}</strong> — the API could not be reached.
      </p>
    );
  }
  return (
    <div className="game-context">
      <section aria-label="Injuries">
        <h3>Injury report</h3>
        <InjurySection injuries={context.injuries} />
      </section>
      <section aria-label="Weather">
        <h3>Kickoff weather</h3>
        <WeatherSection weather={context.weather} />
      </section>
    </div>
  );
}

function InjurySection({
  injuries,
}: {
  injuries: GameContext["injuries"];
}) {
  if (injuries.status !== "ok") {
    return (
      <p className="empty-state">
        <strong>{DATA_UNAVAILABLE}</strong>
        <br />
        {injuries.reason ?? "No injury report rows for these teams/week."}
        <br />
        An empty report list is never shown as &ldquo;no injuries&rdquo;.
      </p>
    );
  }
  const summary = injuries.summary ?? {};
  return (
    <div>
      <p className="injury-summary">
        Out: {summary.Out ?? 0} · Doubtful: {summary.Doubtful ?? 0} ·
        Questionable: {summary.Questionable ?? 0}
        {(summary.Other ?? 0) > 0 ? ` · Other: ${summary.Other}` : ""}
      </p>
      <table className="injury-table">
        <thead>
          <tr>
            <th>Player</th>
            <th>Team</th>
            <th>Pos</th>
            <th>Status</th>
            <th>Injury</th>
            <th>Practice</th>
          </tr>
        </thead>
        <tbody>
          {injuries.records.map((record, i) => (
            <tr key={`${record.full_name}-${i}`}>
              <td>{record.full_name}</td>
              <td>{record.team}</td>
              <td>{record.position ?? "—"}</td>
              <td>{record.report_status ?? "—"}</td>
              <td>{record.report_primary_injury ?? "—"}</td>
              <td>{record.practice_status ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {injuries.note && <p className="context-note">{injuries.note}</p>}
    </div>
  );
}

function WeatherSection({ weather }: { weather: GameContext["weather"] }) {
  if (weather.status !== "ok" || !weather.record) {
    return (
      <p className="empty-state">
        <strong>{DATA_UNAVAILABLE}</strong>
        <br />
        {weather.reason ?? "No weather record for this game."}
      </p>
    );
  }
  const record = weather.record;
  const fmt = (value: number | null, unit: string) =>
    value === null || value === undefined ? "—" : `${value}${unit}`;
  return (
    <div>
      <dl className="weather-grid">
        <div>
          <dt>Conditions</dt>
          <dd>{record.conditions ?? "—"}</dd>
        </div>
        <div>
          <dt>Temperature</dt>
          <dd>{fmt(record.temp_f, "°F")}</dd>
        </div>
        <div>
          <dt>Wind</dt>
          <dd>{fmt(record.wind_mph, " mph")}</dd>
        </div>
        <div>
          <dt>Gusts</dt>
          <dd>{fmt(record.wind_gust_mph, " mph")}</dd>
        </div>
        <div>
          <dt>Precipitation chance</dt>
          <dd>
            {record.precip_prob === null || record.precip_prob === undefined
              ? "—"
              : `${Math.round(record.precip_prob * 100)}%`}
          </dd>
        </div>
        <div>
          <dt>Venue</dt>
          <dd>
            {record.dome === true ? "Dome — outdoor conditions n/a" : (record.venue ?? "—")}
          </dd>
        </div>
      </dl>
      <p className="context-note">
        Kickoff-hour conditions{record.kickoff ? ` for ${record.kickoff}` : ""}.
        Source: {record.source ?? "unknown"}. Null fields are unknown, never
        estimated.
      </p>
    </div>
  );
}
