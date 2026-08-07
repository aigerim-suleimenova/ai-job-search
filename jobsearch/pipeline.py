"""Orchestrating a single search run."""
import json
import os
import threading
import time
import traceback

from . import (config, db, discovery, filters, harvest, i18n, llm, mailer, notify, profiles,
               providers, scoring)
from .collectors import aggregators, ats, crawler

_run_lock = threading.Lock()
_stop = threading.Event()
state = {"running": False, "stage": "", "log": [], "profile": ""}


class Stopped(RuntimeError):
    """The run was interrupted by the user."""


class NothingScored(RuntimeError):
    """Not one job could be scored — there was nothing to think with.

    Collecting jobs needs no model at all: the aggregators are ordinary web
    addresses, and the stages up to triage go through perfectly well without one.
    So a run with a model that had gone reached the very end, wrote down
    "found 340, suitable 0" and finished green — and read exactly like "nothing
    suitable came up today", while in truth not a single job had been looked at.
    """

    def __init__(self, cause: BaseException = None):
        self.cause = cause
        super().__init__("nothing scored")


def request_stop() -> None:
    """Asks the current run to stop at the nearest check point."""
    _stop.set()
    llm.set_cancel()   # long parallel stages break off without waiting for the end


def stop_requested() -> bool:
    return _stop.is_set()


def _check_stop() -> None:
    if _stop.is_set():
        raise Stopped()


def _log(msg: str) -> None:
    state["log"].append(msg)
    if len(state["log"]) > 400:
        del state["log"][:100]



# The order of the run's stages: the page shows "step N of M" from it. Some
# stages are skipped (web search off, the model cannot search), so the numbers
# may jump — which is more honest than drawing a percentage.
STAGE_ORDER = ["stage_cv", "stage_start", "stage_discover", "stage_discover_ats",
               "stage_collect", "stage_dedupe", "stage_prepare", "stage_triage",
               "stage_deep", "stage_deep_research", "stage_save"]
STAGE_COUNT = len(STAGE_ORDER) - 1   # deep and deep_research are the same place

def _logk(key: str, **fmt) -> None:
    """A log line in the interface language.

    A person reads this log — so it cannot always be in Russian, however
    convenient that was while the code was being written.
    """
    lang = config.load()["ui"]["lang"]
    # An exception in a substitution is text for a person too: our own errors
    # carry a translation key, and without this the key's name would land in the log.
    fmt = {k: (i18n.err(lang, v) if isinstance(v, BaseException) else v)
           for k, v in fmt.items()}
    text = i18n.t(lang, key)
    _log(text.format(**fmt) if fmt else text)


def _stage(key: str) -> None:
    """Marks a stage. state holds a translation key rather than finished text:
    the language is filled in by the page that shows it."""
    _check_stop()          # between stages is the safest place to break off
    state["stage"] = key
    state["stage_started"] = time.time()
    state["progress"] = None      # only long stages count items inside themselves
    state["step"] = STAGE_ORDER.index(key) + 1 if key in STAGE_ORDER else state.get("step", 1)
    _log("— " + i18n.t(config.load()["ui"]["lang"], key))


def run_async(trigger: str = "manual", profile: str = None) -> bool:
    """Starts a run in a background thread. False if one is already going."""
    if state["running"]:
        return False
    profile = profile or profiles.active()
    t = threading.Thread(target=run, args=(trigger, profile), daemon=True)
    t.start()
    return True


def prepare_and_run(profile: str, trigger: str = "manual") -> bool:
    """Having the model read the CV takes up to a minute and a half. This used to
    happen right inside the form handler, and all that time a person stared at a
    frozen page with no sign of life. Now the preparation is a visible stage of
    the run like any other."""
    if state["running"]:
        return False

    def worker():
        profiles.set_active(profile)
        state.update(running=True, stage="stage_cv", step=1, log=[], profile=profile)
        _logk("log_cv_parse")
        try:
            cfg = config.load()
            cv = config.cv_text()
            if cv and not (cfg["profile"].get("roles") or cfg["profile"].get("summary")):
                cfg = scoring.profile_from_cv(cfg, cv)
                config.save(cfg)
                _logk("log_cv_roles", roles=cfg["profile"].get("roles", "")[:80])
        except Exception as e:  # noqa: BLE001 — could not parse it; search on what we have
            _logk("log_cv_failed", error=e)
        finally:
            state.update(running=False)
        run(trigger, profile)

    threading.Thread(target=worker, daemon=True).start()
    return True


def run(trigger: str = "manual", profile: str = None) -> None:
    if not _run_lock.acquire(blocking=False):
        return
    if profile:
        profiles.set_active(profile)  # our own thread → set the active profile explicitly
    _stop.clear()
    llm.clear_cancel()
    llm.mark_run_thread()
    state.update(started=time.time(), stage_started=time.time(), progress=None)
    state.update(running=True, stage="stage_start", step=1, log=[], profile=profiles.active())
    cfg = config.load()
    cv = config.cv_text()
    run_id = db.start_run()
    found = fresh = matched_count = 0
    status = "ok"
    coverage = []
    try:
        _logk("log_run", n=run_id, trigger=trigger)

        # 0. Finding new companies for this profile (web search through claude).
        # Only Claude Code CLI can search the web: on other providers this step is
        # skipped, or the model would start inventing companies and links.
        # Either the model searches, or the application does with its own key —
        # the person cannot tell the difference, so one decision covers both.
        web_ok = providers.web_search_possible(cfg)
        n_disc = int(cfg["search"].get("discover_per_run", 0))
        if n_disc > 0 and not web_ok:
            _logk("log_discover_skipped")
            _logk("log_discover_skipped_why")
        if n_disc > 0 and web_ok:
            _stage("stage_discover")
            fresh_companies = discovery.discover(cfg, _log, n=n_disc)
            if fresh_companies:
                cfg["sources"]["companies"] = cfg["sources"].get("companies", []) + fresh_companies
                config.save(cfg)
                for f in fresh_companies:
                    _logk("log_new_company", name=f["name"], url=f["url"])
            else:
                _logk("log_no_new_companies")

        # 0b. Searching for jobs on the ATS domains directly (site:boards.greenhouse.io …)
        # — every hit gives both a job and a new company whose board we then take whole.
        n_ats = int(cfg["search"].get("discover_ats_per_run", 0))
        if n_ats > 0 and web_ok:
            _stage("stage_discover_ats")
            ats_companies = discovery.discover_ats_jobs(cfg, _log, n=n_ats)
            if ats_companies:
                cfg["sources"]["companies"] = cfg["sources"].get("companies", []) + ats_companies
                config.save(cfg)
            else:
                _logk("log_no_new_boards")

        # 1. Collecting
        _stage("stage_collect")
        jobs = []
        def _company(comp):
            name, url = comp.get("name", ""), comp.get("url", "")
            if not url:
                return None
            detected = ats.detect(url)
            entry = {"name": name or url, "url": url,
                     "kind": f"ATS: {detected[0]}" if detected else "crawl",
                     "count": 0, "error": None}
            try:
                if detected:
                    got = ats.fetch(detected[0], detected[1], company_hint=name)
                else:
                    got = crawler.crawl_company(name or url, url, cfg, _log)
                for j in got:  # companies from the person's own list always go to the model
                    j["from_watchlist"] = True
                entry["count"] = len(got)
                _log(f"{name or url} [{entry['kind']}]: {len(got)}")
            except Exception as e:  # noqa: BLE001 — one source must not bring down the run
                reason = i18n.err(cfg["ui"]["lang"], e)
                _log(f"{name or url}: {reason}")
                entry["error"] = reason[:300]
                got = []
            return entry, got

        workers = int(cfg["search"].get("parallelism", 5))
        for r in llm.pmap(_company, cfg["sources"].get("companies", []), workers=workers):
            if isinstance(r, Exception) or r is None:
                continue
            entry, got = r
            coverage.append(entry)
            jobs += got
        jobs += aggregators.collect(cfg, _log, coverage)
        found = len(jobs)
        _logk("log_collected", n=found)

        # Every single source failed — that is not "there are no jobs", that is
        # "nothing reached us". The run reported success all the same, and the
        # person read the zero as the market's answer. The cause of this is
        # usually one shared, external thing: an antivirus intercepting
        # connections, a corporate gateway, no network at all. One trouble, and
        # it looked like a verdict on their profession.
        sources = [c for c in coverage if c.get("kind") == "aggregator"]
        if sources and all(c.get("error") for c in sources):
            status = "warn"
            _logk("log_all_sources_failed", n=len(sources))

        # 2. Normalising and de-duplicating (direct jobs win over the rest)
        _stage("stage_dedupe")
        by_key = {}
        for j in jobs:
            j["key"] = filters.job_key(j.get("company", ""), j.get("title", ""))
            prev = by_key.get(j["key"])
            if prev is None or (j.get("is_direct") and not prev.get("is_direct")):
                by_key[j["key"]] = j
        jobs = list(by_key.values())

        # 3. Hard filters (with examples of what was dropped in the log, to catch false hits)
        wanted = filters.parse_locations(cfg["search"].get("locations", ""))
        exclude = [t.strip().lower() for t in cfg["search"].get("keywords_exclude", "").split(",") if t.strip()]
        include_remote = bool(cfg["search"].get("include_remote", True))
        since = str(cfg["search"].get("posted_from", "") or "")
        until = str(cfg["search"].get("posted_to", "") or "")
        kept, drop_loc, drop_kw, drop_date = [], [], [], []
        for j in jobs:
            if not filters.location_ok(j.get("location", ""), wanted, include_remote):
                drop_loc.append(j)
            elif filters.has_excluded(j, exclude):
                drop_kw.append(j)
            elif not filters.posted_ok(j, since, until):
                drop_date.append(j)
            else:
                kept.append(j)
        jobs = kept
        if drop_date:
            _logk("log_drop_dates", n=len(drop_date), since=since or "…", until=until or "…")
        for j in jobs:
            # What the posting itself said about the right to work. We read it
            # from the words rather than asking the model: it was asked, and it
            # stayed silent in all one hundred and nine cases in a row.
            j["visa"] = filters.visa_stance(j)
            if filters.looks_like_agency(j.get("company", "")):
                j["is_agency"] = True
        _logk("log_after_filters", n=len(jobs), loc=len(drop_loc), kw=len(drop_kw))
        for j in drop_loc[:5]:
            _logk("log_drop_location", title=j.get("title", "")[:60], loc=j.get("location", "")[:60])
        for j in drop_kw[:5]:
            _logk("log_drop_keyword", title=j.get("title", "")[:60], company=j.get("company", "")[:40])

        # 3a. Employers from the links: we already hold their addresses, so no extra
        # requests. Done before the "only new ones" filter — a board is worth having
        # even when the job itself is old news: the aggregator showed one, the board
        # has twenty.
        if cfg["sources"].get("harvest_boards", True):
            found_boards = harvest.find_new(jobs, cfg["sources"].get("companies", []),
                                            limit=int(cfg["sources"].get("harvest_per_run", 10)))
            if found_boards:
                cfg["sources"]["companies"] = (list(cfg["sources"].get("companies", []))
                                               + found_boards)
                config.save(cfg)
                for c in found_boards:
                    _logk("log_harvest_board", name=c["name"], url=c["url"])
                _logk("log_harvest_total", n=len(found_boards))

        # 4. Only the new ones
        seen = db.seen_keys()
        jobs = [j for j in jobs if j["key"] not in seen]
        fresh = len(jobs)
        _logk("log_fresh", n=fresh)

        # 5. Order and an upper bound on triage (running it in parallel, we get through
        # everything). Jobs from watched companies come first, then by wording.
        _stage("stage_prepare")
        terms = scoring.profile_terms(cfg, cv)

        # 5a. A cheap pre-filter: cut off the obviously wrong trade (sales/HR/support)
        # BEFORE the expensive model call — it frees the triage budget for real candidates.
        if cfg["search"].get("drop_off_target", True):
            before = len(jobs)
            jobs = [j for j in jobs if not filters.off_target(j, terms)]
            dropped = before - len(jobs)
            # The dropped ones are NOT marked as seen: the check is free (strings, no
            # model) and will happen again next run, and if the profile's roles or
            # skills change those jobs come back into consideration by themselves.
            if dropped:
                _logk("log_dropped_offtarget", n=dropped)

        for j in jobs:
            j["_lex"] = scoring.lexical_score(j, terms)
        limit = int(cfg["search"].get("triage_limit", 400))
        # Three queues, not two. The middle one is what a source found FOR OUR
        # query, rather than dumped from its whole feed.
        #
        # Without it, this is what happened. The order comes from how the words
        # match the profile, while EURES searches by meaning and brings back
        # titles in their own languages: for "seamstress", the Polish
        # "Szwaczka". They share no letters, the match is zero, and all two
        # hundred and eighty-three of its jobs fell below the line — under four
        # hundred IT jobs from programmer boards, which got there because the
        # word "Designer" happened to be in the title.
        #
        # What was found by query has already passed somebody else's filter —
        # one that understands languages and meaning. Putting it after an
        # accidental match would mean trusting our own letter-counting more than
        # the EURES search engine.
        by_words = lambda j: -j["_lex"]  # noqa: E731
        watch = sorted((j for j in jobs if j.get("from_watchlist")), key=by_words)
        asked = sorted((j for j in jobs
                        if not j.get("from_watchlist") and j.get("by_query")), key=by_words)
        others = sorted((j for j in jobs
                         if not j.get("from_watchlist") and not j.get("by_query")), key=by_words)
        in_order = watch + asked + others
        candidates = in_order[:limit]
        deferred = in_order[limit:]  # not marked seen — they get their turn next run
        _logk("log_to_triage", n=len(candidates), own=min(len(watch), limit))
        if deferred:
            _logk("log_deferred", n=len(deferred))

        # 6. Model triage (haiku, in parallel)
        _stage("stage_triage")
        if not (cfg["profile"].get("roles") or cfg["profile"].get("summary") or cv):
            _logk("log_empty_profile")
            _logk("log_empty_profile_fix")
        elif cv and not (cfg["profile"].get("roles") or cfg["profile"].get("summary")):
            # There is a CV, but no profile came out of it — and that is the
            # worst case, because from the outside it looks fine. Parsing the CV
            # is a single call to the model, and a weak one can fail it; then no
            # examples get built for scoring, an empty query goes to the sources,
            # and the scores degenerate. On a real run all thirty-seven jobs came
            # out at exactly sixty percent — every one of them with a "verified"
            # tick. The old check did not catch this: the presence of a CV was
            # enough for it.
            status = "warn"
            _logk("log_profile_unparsed")
            _logk("log_profile_unparsed_fix")
        # Saved to the database as they are scored: a person may have opened
        # "Results" right after starting, and waiting out the whole run to see the
        # first line would be poor.
        def _save_batch(batch, done=0, total=0):
            for j in batch:
                if j.get("score") is not None:
                    db.save_job(j, run_id)
            if total:
                state["progress"] = {"done": done, "total": total}

        failures = scoring.triage(candidates, cfg, _log, cv=cv, on_batch=_save_batch)
        threshold = int(cfg["search"].get("threshold", 70))
        scored = [j for j in candidates if j.get("score") is not None]
        if candidates and not scored:
            # There was something to score, and nothing got scored. No reason to
            # go further: below is only the mail saying nothing suitable turned up.
            raise NothingScored(failures[0] if failures else None)
        if failures:
            # Some batches did not answer — the jobs in them are not lost for
            # good (unscored, they are not marked seen and come back on the next
            # run), but the run is no longer complete, and calling it complete
            # would be wrong.
            status = "warn"
            _logk("log_triage_partial", failed=len(failures), missed=len(candidates) - len(scored))

        # 6b. A second opinion for the grey zone. Triage is noisy to ±15-20 points;
        # an overestimate is corrected later by the deep analysis, but an
        # UNDERestimate is beyond repair: the job is stored with a low score and
        # never looked at again. So everything that landed noticeably below the
        # threshold gets a second independent vote, and we take the higher of the two.
        if cfg["search"].get("triage_second_vote", True):
            band = [j for j in scored if threshold - 40 <= (j["score"] or 0) < threshold]
            if band:
                _logk("log_second_vote", n=len(band), lo=threshold - 40, hi=threshold - 1)
                first = {j["key"]: (j["score"], j.get("reason", "")) for j in band}
                scoring.triage(band, cfg, _log, cv=cv)
                rescued = 0
                for j in band:
                    s1, r1 = first[j["key"]]
                    if (j.get("score") or 0) < (s1 or 0):  # the first vote was higher — keep it
                        j["score"], j["reason"] = s1, r1
                    if (j.get("score") or 0) >= threshold and (s1 or 0) < threshold:
                        rescued += 1
                if rescued:
                    _logk("log_second_vote_rescued", n=rescued)

        # Triage (haiku) is systematically optimistic and noisy to ±15-20 points.
        # EVERY job triage lifted to the threshold gets the deep check — those are
        # what make up the digest, and without the check false passes seep into it.
        # Plus a margin of near-misses just below the threshold, to catch triage's
        # underestimates (as with Matcha, 70→82).
        keep_margin = 20
        above = sorted((j for j in scored if j["score"] >= threshold),
                       key=lambda j: (not j.get("from_watchlist"), -j["score"]))
        near = sorted((j for j in scored if threshold - keep_margin <= j["score"] < threshold),
                      key=lambda j: (not j.get("from_watchlist"), -j["score"]))
        top_n = int(cfg["search"].get("deep_top_n", 15))
        max_deep = max(top_n, len(above)) + 5  # a soft ceiling, so as not to go broke
        # The deep analysis can be turned off: then the run only scores, and a
        # person asks for a particular job with the button next to it in the list.
        to_deep = (above + near[:top_n])[:max_deep] if cfg["search"].get("deep_during_run", True) else []
        for j in scored:  # the rest are stored straight away with their triage score
            if j not in to_deep:
                db.save_job(j, run_id)
        if to_deep:
            _logk("log_triage_done", n=len(scored), above=len(above),
                  near=len(to_deep) - len(above), margin=threshold - keep_margin)
        else:
            _logk("log_triage_done_nodeep", n=len(scored), above=len(above))

        # 7. Deep analysis (in parallel): the exact %, edits for the CV and LinkedIn,
        # plus, if enabled, the salary and facts about the company from a web search
        # Researching a company is a web search. Asking for one from a model that
        # cannot reach the network wastes the call and nudges it into making
        # things up: it has to answer with something. The setting says "I want
        # this", and whether it is possible is the provider's business.
        research = bool(cfg["search"].get("research_company", True)) and web_ok
        if cfg["search"].get("research_company", True) and not web_ok:
            _logk("log_research_skipped")
        _stage("stage_deep_research" if research else "stage_deep")
        deep_done = {"n": 0}

        def _deep(j):
            was = j.get("score")
            scoring.deep_analyze(j, cfg, cv, _log, research=research)
            deep_done["n"] += 1
            tail = f" ({was}%→{j.get('score')}%)" if j.get("score") != was else ""
            state["progress"] = {"done": deep_done["n"], "total": len(to_deep)}
            _logk("log_deep_item", i=deep_done["n"], total=len(to_deep),
                  title=j.get("title"), company=j.get("company"), tail=tail)

        # deep calls are heavy (fetching a page plus a long prompt) — parallelise more carefully
        deep_workers = min(int(cfg["search"].get("parallelism", 5)), 3)
        for r in llm.pmap(_deep, to_deep, workers=deep_workers):
            if isinstance(r, Exception):
                deep_done["n"] += 1
                _logk("log_deep_error", error=r)
        for j in to_deep:
            db.save_job(j, run_id)

        matched = [j for j in scored if (j.get("score") or 0) >= threshold]
        matched.sort(key=lambda j: (not (j.get("is_direct") and not j.get("is_agency")), -(j.get("score") or 0)))
        final = matched
        # "close but below the threshold" for the digest — analysed ones only, so triage does not add noise
        demoted = sorted(
            (j for j in to_deep if (j.get("score") or 0) < threshold and (j.get("score") or 0) >= threshold - 15),
            key=lambda j: -(j.get("score") or 0),
        )[:5]
        matched_count = len(final)

        # 8. Saving and notifying
        _stage("stage_save")
        for j in matched:
            db.save_job(j, run_id)
        base_url = os.environ.get("AIJS_BASE_URL", "http://127.0.0.1:8765")
        digest = notify.format_digest(final, threshold, base_url=base_url, demoted=demoted,
                                      lang=cfg.get("ui", {}).get("output_lang", "ru"))
        if cfg["telegram"].get("bot_token") and cfg["telegram"].get("chat_id"):
            try:
                notify.send_message(cfg, digest)
                _logk("log_tg_sent")
            except RuntimeError as e:
                _log(f"Telegram: {e}")
                status = "warn"
        else:
            _logk("log_tg_not_set")
        if mailer.configured(cfg):
            try:
                mailer.send_digest(cfg, final, profiles.name_of(profiles.active()),
                                   db.now(), threshold)
                _logk("log_mail_sent")
            except mailer.MailError as e:
                _logk("log_mail_error", error=e)
                status = "warn"
        _logk("log_done")
    except (Stopped, llm.Cancelled):
        status = "stopped"
        _logk("log_stopped")
    except llm.AuthError as e:
        # Jobs can be collected without a model, but not scored, so the run stops
        # here rather than quietly reaching the end with nothing.
        status = "noauth"
        _logk("log_noauth", error=e)
        _logk("log_noauth_stop")
    except NothingScored as e:
        # The same trouble as above, only the model said nothing rather than
        # asking to sign in — it had been deleted, or was never started. The run
        # used to walk past this and finish green.
        status = "error"
        _logk("log_nothing_scored")
        if e.cause is not None:
            _logk("log_nothing_scored_why", error=e.cause)
    except Exception:  # noqa: BLE001
        status = "error"
        _log(i18n.t(config.load()["ui"]["lang"], "log_error") + "\n" + traceback.format_exc(limit=6))
    finally:
        db.finish_run(run_id, found, fresh, matched_count, status, "\n".join(state["log"]),
                      coverage=json.dumps(coverage, ensure_ascii=False))
        state.update(running=False, stage="")
        _stop.clear()
        llm.clear_cancel()
        _run_lock.release()
