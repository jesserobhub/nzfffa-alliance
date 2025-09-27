#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Alliance Weekly Fantasy Newsletter Generator for Sleeper
(Ron Burgundy energy, team-level banter only)

Outputs: <LEAGUE_NAME>_Weeks<first>_<last>_Recap.pdf
Config: SLEEPER_LEAGUE_ID env (defaults to 1180243382958317568)
Deps: requests, reportlab
"""

import os, re, math, random, datetime
from collections import defaultdict
import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import inch

BASE = "https://api.sleeper.app/v1"

def get_json(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def get_league(league_id):   return get_json(f"{BASE}/league/{league_id}")
def get_users(league_id):    return get_json(f"{BASE}/league/{league_id}/users")
def get_rosters(league_id):  return get_json(f"{BASE}/league/{league_id}/rosters")
def get_matchups(league_id, week): return get_json(f"{BASE}/league/{league_id}/matchups/{week}")

def is_week_completed(matchups_for_week):
    if not matchups_for_week: return False
    by_id = defaultdict(list); nonzero = False
    for m in matchups_for_week:
        if "matchup_id" not in m: return False
        by_id[m["matchup_id"]].append(m)
        pts = float(m.get("points", 0) or 0.0)
        if pts > 0: nonzero = True
    if not nonzero: return False
    return all(len(v) >= 2 for v in by_id.values())

def build_id_maps(users, rosters):
    user_map = {u["user_id"]: u for u in users}
    owner_display = {u["user_id"]: (u.get("metadata") or {}).get("team_name") or u.get("display_name") or "Unknown" for u in users}
    roster_to_owner, roster_to_teamname = {}, {}
    for r in rosters:
        rid = r["roster_id"]; owner_id = r.get("owner_id"); roster_to_owner[rid] = owner_id
        team_name = (r.get("metadata") or {}).get("team_name") \
                    or (user_map.get(owner_id, {}).get("metadata") or {}).get("team_name") \
                    or owner_display.get(owner_id, "Unknown")
        roster_to_teamname[rid] = team_name
    return roster_to_teamname, owner_display, roster_to_owner

def pair_matchups(matchups_for_week):
    by = defaultdict(list)
    for m in matchups_for_week: by[m["matchup_id"]].append(m)
    pairs = []
    for entries in by.values():
        if len(entries) >= 2:
            e = sorted(entries, key=lambda x: x.get("roster_id"))
            a, b = e[0], e[1]
            ra, rb = a.get("roster_id"), b.get("roster_id")
            pa, pb = float(a.get("points", 0) or 0.0), float(b.get("points", 0) or 0.0)
            pairs.append((ra, pa, rb, pb))
    return pairs

def collect_weeks_completed(league_id, max_weeks=30):
    completed = []
    for w in range(1, max_weeks+1):
        try:
            mu = get_matchups(league_id, w)
        except requests.HTTPError:
            break
        if mu and is_week_completed(mu):
            completed.append((w, pair_matchups(mu)))
    return completed

def compute_team_level_stats(roster_to_name, weeks_pairs):
    standings, weekly_points, schedule = {}, defaultdict(dict), defaultdict(list)
    for rid in roster_to_name:
        standings[rid] = dict(team=roster_to_name[rid], W=0.0, L=0.0, PF=0.0, PA=0.0, Diff=0.0, GP=0)
    for week, pairs in weeks_pairs:
        for ra, pa, rb, pb in pairs:
            weekly_points[week][ra] = pa; weekly_points[week][rb] = pb
        for ra, pa, rb, pb in pairs:
            schedule[ra].append(rb); schedule[rb].append(ra)
            standings[ra]["PF"] += pa; standings[ra]["PA"] += pb
            standings[rb]["PF"] += pb; standings[rb]["PA"] += pa
            standings[ra]["GP"] += 1;  standings[rb]["GP"] += 1
            if pa > pb: standings[ra]["W"] += 1; standings[rb]["L"] += 1
            elif pb > pa: standings[rb]["W"] += 1; standings[ra]["L"] += 1
            else: standings[ra]["W"] += .5; standings[rb]["W"] += .5; standings[ra]["L"] += .5; standings[rb]["L"] += .5
    for rid in standings: standings[rid]["Diff"] = standings[rid]["PF"] - standings[rid]["PA"]
    return standings, weekly_points, schedule

def compute_all_play(weekly_points):
    per_team_week = defaultdict(list)
    for _, pts_map in weekly_points.items():
        items = list(pts_map.items())
        for rid, pts in items:
            wins = 0.0; total = 0.0
            for orid, opts in items:
                if orid == rid: continue
                total += 1
                wins += 1 if pts > opts else (0.5 if pts == opts else 0)
            if total: per_team_week[rid].append(wins/total)
    return {rid: (sum(v)/len(v) if v else 0.0) for rid, v in per_team_week.items()}

def compute_sos(schedule, standings):
    pfpg = {rid: (st["PF"]/(st["GP"] if st["GP"]>0 else 1)) for rid, st in standings.items()}
    return {rid: (sum(pfpg[o] for o in opps)/len(opps) if opps else 0.0) for rid, opps in schedule.items()}

def fmt_num(x, nd=2): return f"{x:.{nd}f}"

def luck_badge(luck_val):
    styles = getSampleStyleSheet()
    if luck_val > 0.5:  col, icon = "#0a8a0a", "🍀"
    elif luck_val < -0.5: col, icon = "#c1121f", "😬"
    else: col, icon = "#6b7280", "⚖️"
    return Paragraph(f"<font color='{col}'>{icon} {fmt_num(luck_val,2)}</font>", styles["BodyText"])

def sorted_rows(rows): return sorted(rows, key=lambda r: (r["W"], r["PF"]), reverse=True)

def make_standings_table(standings):
    rows = [dict(Team=st["team"], W=st["W"], L=st["L"], PF=st["PF"], PA=st["PA"], Diff=st["Diff"]) for st in standings.values()]
    rows = sorted_rows(rows)
    data = [["Team","W","L","PF","PA","Diff"]]
    for r in rows:
        data.append([r["Team"],
                     f"{float(r['W']):.0f}" if float(r["W"]).is_integer() else fmt_num(r["W"],1),
                     f"{float(r['L']):.0f}" if float(r["L"]).is_integer() else fmt_num(r["L"],1),
                     fmt_num(r["PF"]), fmt_num(r["PA"]), fmt_num(r["Diff"])])
    tbl = Table(data, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1f2937")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),0.5,colors.grey),("ALIGN",(1,1),(-1,-1),"RIGHT"),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.whitesmoke, colors.Color(0.96,0.96,0.96)])
    ]))
    return tbl

def make_sos_luck_table(standings, all_play, sos):
    rows = []
    for rid, st in standings.items():
        gp = st["GP"]; ap = all_play.get(rid,0.0); expw = ap*gp; luck = st["W"]-expw
        rows.append(dict(Team=st["team"], W=st["W"], L=st["L"], PF=st["PF"], PA=st["PA"], SOS=sos.get(rid,0.0),
                         AllPlay=ap, ExpW=expw, Luck=luck))
    league_avg_sos = sum(r["SOS"] for r in rows)/len(rows) if rows else 0.0
    rows = sorted(rows, key=lambda r:(r["W"], r["PF"]), reverse=True)

    data = [["Team","W","L","PF","PA","SOS (OppAvg)","All-Play%","Exp W","Luck"]]
    for r in rows:
        data.append([r["Team"],
                     f"{float(r['W']):.0f}" if float(r["W"]).is_integer() else fmt_num(r["W"],1),
                     f"{float(r['L']):.0f}" if float(r["L"]).is_integer() else fmt_num(r["L"],1),
                     fmt_num(r["PF"]), fmt_num(r["PA"]), fmt_num(r["SOS"]),
                     fmt_num(r["AllPlay"]*100,1)+"%", fmt_num(r["ExpW"],2), luck_badge(r["Luck"])])
    data.append(["League Avg","-","-","-","-", fmt_num(league_avg_sos),"-","-",
                 Paragraph("<font color='#6b7280'>—</font>", getSampleStyleSheet()["BodyText"])])
    tbl = Table(data, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1f2937")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),0.5,colors.grey),("ALIGN",(1,1),(-2,-2),"RIGHT"),
        ("VALIGN",(-1,1),(-1,-2),"MIDDLE"),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("ROWBACKGROUNDS",(0,1),(-1,-2),[colors.whitesmoke, colors.Color(0.96,0.96,0.96)]),
        ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#e5e7eb")),
    ]))
    return tbl, rows, league_avg_sos

def week_highlights(weeks_pairs, r2n):
    heart, blow, overall_closest, overall_blowout = {}, {}, None, None
    for week, pairs in weeks_pairs:
        closest, biggest = None, None
        for ra, pa, rb, pb in pairs:
            margin = abs(pa - pb); pair = (r2n[ra], pa, r2n[rb], pb, margin)
            if closest is None or margin < closest[-1]: closest = pair
            if biggest is None or margin > biggest[-1]: biggest = pair
        if closest: heart[week] = closest
        if biggest: blow[week] = biggest
        if closest and (overall_closest is None or closest[-1] < overall_closest[-1]): overall_closest = (week,) + closest
        if biggest and (overall_blowout is None or biggest[-1] > overall_blowout[-1]): overall_blowout = (week,) + biggest
    return heart, blow, overall_closest, overall_blowout

def pick_line(cat, teams):
    t = ", ".join(teams) if isinstance(teams,(list,tuple)) else str(teams)
    bank = {
        "Top Dogs":[f"{t} remain classier than a mahogany scotch cabinet—still undefeated.",
                    f"Stay classy: {t} haven’t tasted defeat yet.",
                    f"{t} are on a heater so hot it needs an SPF rating.",
                    f"Undefeated and unbothered: {t} keep jazz-fluting to victory.",
                    f"{t} are so dominant the league asked for a wellness check."],
        "Doormats":[f"{t} keep holding the door like courteous bellhops—wins not included.",
                    f"It’s brisk out; {t} brought the L-sweaters again.",
                    f"{t} are allergic to the letter W—someone call the pharmacist.",
                    f"The rebuild is on schedule… if the schedule is 2087. Chin up, {t}.",
                    f"{t} promise they’re fine. They said that through a smile."],
        "Luckiest":[f"{t} have more horseshoes than a Kentucky derby—variance loves you.",
                    f"Lady Luck keeps texting {t} back. Respect.",
                    f"The fantasy gods winked at {t}; results ensued.",
                    f"{t} caught all the bounces like they’re spring-loaded.",
                    f"{t} are living proof that fortune favors the fabulous."],
        "Unluckiest":[f"{t} stepped on every rake in the lawn—hang in there.",
                      f"The stat gremlins keep nibbling at {t}’s ankles.",
                      f"{t} have earned a ceremonial re-roll from the universe.",
                      f"If pain built character, {t} would be prestige TV.",
                      f"{t} keep drawing short straws in an industrial straw factory."],
        "Easiest":[f"{t} found the travelator—schedule slants downhill nicely.",
                   f"The path is paved in velvet for {t}. Enjoy the glide.",
                   f"{t} are sipping umbrella drinks on Schedule Beach.",
                   f"Matchups part like curtains for {t}. Encore!",
                   f"{t} booked the deluxe itinerary—minimal turbulence."],
        "Hardest":[f"{t} took the scenic route through Mordor—respect the grind.",
                   f"Every week is leg day for {t}. Quads of steel.",
                   f"{t} keep drawing boss fights on Nightmare difficulty.",
                   f"The gauntlet respects {t}, even if the standings don’t.",
                   f"{t} brought a jazz flute to a knife fight and still swingin’."]
    }
    return random.choice(bank[cat])

_ILLEGAL = r'[^A-Za-z0-9._ -]'
def safe_filename(name: str) -> str:
    name = re.sub(_ILLEGAL, "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    for ch in ["/","\\","|",":","*","?","\"","<",">","\0"]:
        name = name.replace(ch,"_")
    return name or "League"

def build_story(styles, standings, all_play, sos, completed, roster_to_name, league_name, out_name):
    story = []
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    first_week = completed[0][0] if completed else "NA"
    last_week  = completed[-1][0] if completed else "NA"
    story.append(Paragraph(f"{league_name} — Weeks {first_week}–{last_week} Recap", styles["Hed"]))
    story.append(Paragraph(f"Generated {today}", styles["TinyGray"]))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("Standings", styles["SubHed"]))
    story.append(make_standings_table(standings))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("Strength of Schedule & Luck", styles["SubHed"]))
    sos_tbl, sos_rows_sorted, _ = make_sos_luck_table(standings, all_play, sos)
    story.append(sos_tbl)
    story.append(Spacer(1, 0.2*inch))

    heart, blow, overall_close, overall_blow = week_highlights(completed, roster_to_name)
    story.append(Paragraph("Highlights", styles["SubHed"]))
    if heart:
        lines = [f"Week {wk}: {a} {fmt_num(pa,1)} vs {b} {fmt_num(pb,1)} — margin {fmt_num(m,1)}"
                 for wk,(a,pa,b,pb,m) in sorted(heart.items())]
        story.append(Paragraph("<b>Heart-Attack Matchups</b><br/>" + "<br/>".join(lines), styles["BodyText"]))
    else:
        story.append(Paragraph("<b>Heart-Attack Matchups</b><br/>No nail-biters yet.", styles["BodyText"]))
    story.append(Spacer(1, 0.1*inch))
    if blow:
        lines = [f"Week {wk}: {a} {fmt_num(pa,1)} vs {b} {fmt_num(pb,1)} — margin {fmt_num(m,1)}"
                 for wk,(a,pa,b,pb,m) in sorted(blow.items())]
        story.append(Paragraph("<b>Blowouts of the Week</b><br/>" + "<br/>".join(lines), styles["BodyText"]))
    else:
        story.append(Paragraph("<b>Blowouts of the Week</b><br/>No blowouts recorded.", styles["BodyText"]))
    story.append(Spacer(1, 0.2*inch))

    # Banter + story paragraph
    rows = [{"Team": st["team"], "W": st["W"], "PF": st["PF"], "GP": st["GP"], "RID": rid} for rid, st in standings.items()]
    standings_sorted = sorted(rows, key=lambda r:(r["W"], r["PF"]), reverse=True)
    undefeated = [r["Team"] for r in rows if r["GP"]>0 and math.isclose(r["W"], r["GP"], rel_tol=1e-9)]
    winless    = [r["Team"] for r in rows if r["GP"]>0 and math.isclose(r["W"], 0.0,     rel_tol=1e-9)]
    luck_list = [{"Team": r["Team"], "Luck": (r["W"] - all_play.get(r["RID"],0.0)*r["GP"])} for r in rows]
    luckiest_sorted = sorted(luck_list, key=lambda x:x["Luck"], reverse=True)
    unluckiest_sorted = sorted(luck_list, key=lambda x:x["Luck"])
    luckiest_teams   = [luckiest_sorted[0]["Team"]] if luckiest_sorted else []
    unluckiest_teams = [unluckiest_sorted[0]["Team"]] if unluckiest_sorted else []
    sos_only = [{"Team": standings[rid]["team"], "SOS": sos.get(rid,0.0)} for rid in standings.keys()]
    sos_only_sorted = sorted(sos_only, key=lambda x:x["SOS"])
    easiest_teams = [sos_only_sorted[0]["Team"]] if sos_only_sorted else []
    hardest_teams = [sos_only_sorted[-1]["Team"]] if sos_only_sorted else []

    for hed, line in [
        ("<b>Top Dogs</b>",       pick_line("Top Dogs",    undefeated or ["(none)"])),
        ("<b>Doormats</b>",       pick_line("Doormats",    winless or ["(none)"])),
        ("<b>Luckiest</b>",       pick_line("Luckiest",    luckiest_teams) if luckiest_teams else None),
        ("<b>Unluckiest</b>",     pick_line("Unluckiest",  unluckiest_teams) if unluckiest_teams else None),
        ("<b>Easiest Schedule</b>", pick_line("Easiest",   easiest_teams) if easiest_teams else None),
        ("<b>Hardest Schedule</b>", pick_line("Hardest",   hardest_teams) if hardest_teams else None),
    ]:
        if line:
            story.append(Paragraph(f"{hed}<br/>{line}", styles["BodyText"]))
            story.append(Spacer(1, 0.08*inch))
    story.append(Spacer(1, 0.15*inch))

    def story_para():
        # minimal, robust story (no crashes if empty)
        top_team = standings_sorted[0]["Team"] if standings_sorted else "—"
        bottom_team = standings_sorted[-1]["Team"] if standings_sorted else "—"
        pf_leader = max(standings_sorted, key=lambda r: r["PF"])["Team"] if standings_sorted else "—"
        pf_laggard = min(standings_sorted, key=lambda r: r["PF"])["Team"] if standings_sorted else "—"
        easiest = sos_only_sorted[0]["Team"] if sos_only_sorted else "—"
        hardest = sos_only_sorted[-1]["Team"] if sos_only_sorted else "—"
        luckiest = luckiest_teams[0] if luckiest_teams else "—"
        unluckiest = unluckiest_teams[0] if unluckiest_teams else "—"
        if overall_close:
            w,a,pa,b,pb,m = overall_close
            close_txt = f"closest finish was Week {w}: {a} {fmt_num(pa,1)} vs {b} {fmt_num(pb,1)} (margin {fmt_num(m,1)})."
        else:
            close_txt = "no nail-biters recorded yet."
        if overall_blow:
            w,a,pa,b,pb,m = overall_blow
            blow_txt = f"biggest blowout was Week {w}: {a} {fmt_num(pa,1)} vs {b} {fmt_num(pb,1)} (margin {fmt_num(m,1)})."
        else:
            blow_txt = "no blowouts on record yet."
        return Paragraph(
            f"In the Alliance, {top_team} lead while {bottom_team} look to rally. "
            f"PF cannon: {pf_leader}; quiet cannons: {pf_laggard}. "
            f"Schedules favor {easiest} and test {hardest}. "
            f"Luck smiles on {luckiest}, frowns on {unluckiest}. "
            f"For drama lovers: {close_txt} For chaos enjoyers: {blow_txt} Stay classy.",
            getSampleStyleSheet()["BodyText"]
        )
    story.append(Paragraph("League Story So Far", getSampleStyleSheet()["Heading4"]))
    heart, blow, overall_close, overall_blow = week_highlights(completed, roster_to_name)
    story.append(story_para())
    return story

def main():
    league_id = (os.environ.get("SLEEPER_LEAGUE_ID") or "1180243382958317568").strip()
    print(f"[DEBUG] Using league_id={league_id!r}")
    try:
        if not league_id.isdigit():
            raise ValueError(f"SLEEPER_LEAGUE_ID looks invalid: '{league_id}'")
        league = get_league(league_id)
        league_name = league.get("name", f"League_{league_id}")
        users = get_users(league_id)
        rosters = get_rosters(league_id)
        roster_to_name, _, _ = build_id_maps(users, rosters)
        completed = collect_weeks_completed(league_id)
        standings, weekly_points, schedule = compute_team_level_stats(roster_to_name, completed)
        all_play = compute_all_play(weekly_points)
        sos = compute_sos(schedule, standings)

        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="Hed", fontName="Helvetica-Bold", fontSize=16, spaceAfter=8))
        styles.add(ParagraphStyle(name="SubHed", fontName="Helvetica-Bold", fontSize=12, spaceAfter=6))
        styles.add(ParagraphStyle(name="TinyGray", fontSize=8, textColor=colors.grey))

        safe_league = safe_filename(league_name)
        first_week = completed[0][0] if completed else "NA"
        last_week  = completed[-1][0] if completed else "NA"
        out_name = f"{safe_league}_Weeks{first_week}_{last_week}_Recap.pdf"

        story = []
        # build robustly
        story.extend(build_story(styles, standings, all_play, sos, completed, roster_to_name, league_name, out_name))

        doc = SimpleDocTemplate(out_name, pagesize=LETTER, leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=40)
        doc.build(story)
        print(f"[DEBUG] Generated: {out_name}")

    except Exception as e:
        # absolutely-always produce a fallback PDF so the workflow can upload something
        league_name = f"League_{league_id}"
        out_name = f"{safe_filename(league_name)}_WeeksNA_NA_Recap.pdf"
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="Hed", fontName="Helvetica-Bold", fontSize=16, spaceAfter=8))
        doc = SimpleDocTemplate(out_name, pagesize=LETTER, leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=40)
        story = [
            Paragraph(f"{league_name} — Weekly Recap", styles["Hed"]),
            Paragraph("Data unavailable during generation. Try rerunning the workflow.", styles["BodyText"]),
            Paragraph(f"Error: {e}", styles["BodyText"]),
        ]
        doc.build(story)
        print(f"[DEBUG] Generated (fallback): {out_name}")

if __name__ == "__main__":
    main()
