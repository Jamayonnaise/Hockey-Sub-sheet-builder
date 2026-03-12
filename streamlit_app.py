import streamlit as st
import json
import math

st.set_page_config(page_title="Hockey Manager", page_icon="🏑", layout="wide")

# ── constants ────────────────────────────────────────────────────────────────

POS = {
    "GK":  {"label": "Goalkeeper",  "color": "#d97706"},
    "CD":  {"label": "Centre Def",  "color": "#7c3aed"},
    "WD":  {"label": "Wing Def",    "color": "#2563eb"},
    "MID": {"label": "Midfielder",  "color": "#059669"},
    "ATT": {"label": "Attacker",    "color": "#dc2626"},
}
POS_ORDER = ["GK", "CD", "WD", "MID", "ATT"]

FORMATIONS = {
    "4-3-3": {"GK": 1, "CD": 2, "WD": 2, "MID": 3, "ATT": 3},
    "4-4-2": {"GK": 1, "CD": 2, "WD": 2, "MID": 4, "ATT": 2},
    "3-3-4": {"GK": 1, "CD": 1, "WD": 2, "MID": 3, "ATT": 4},
    "5-3-2": {"GK": 1, "CD": 3, "WD": 2, "MID": 3, "ATT": 2},
    "3-5-2": {"GK": 1, "CD": 1, "WD": 2, "MID": 5, "ATT": 2},
}

TEST_NAMES = [
    ("1",  "Sarah Johnson"), ("2",  "Emma Clarke"),   ("3",  "Olivia Wright"),
    ("4",  "Mia Thompson"),  ("5",  "Ava Robinson"),  ("6",  "Isla Harris"),
    ("7",  "Grace Martin"),  ("8",  "Ruby Scott"),    ("9",  "Zoe Wilson"),
    ("10", "Lily Anderson"), ("11", "Sophie White"),  ("12", "Chloe Davies"),
    ("13", "Freya Evans"),   ("14", "Poppy Turner"),  ("15", "Imogen Baker"),
    ("16", "Amber Phillips"),("17", "Heidi Morgan"),  ("18", "Niamh Hughes"),
]

# ── helpers ──────────────────────────────────────────────────────────────────

def ini(name):
    parts = name.strip().split()
    return "".join(p[0].upper() for p in parts if p)[:3]

def merge_segs(segs):
    if not segs:
        return []
    segs = sorted(segs)
    merged = [list(segs[0])]
    for s, e in segs[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [tuple(x) for x in merged]

def auto_generate(squad_players, pos_map, total, brk, qm, fmt):
    """
    Generate a fair substitution schedule.

    Logic:
    - Sub opportunities are every `brk` minutes within each quarter.
    - Each position group is staggered by (index * brk) minutes so their
      sub windows are spread evenly through the quarter and never clash.
    - Only ONE swap per sub opportunity (one player on, one off).
    - The only constraint for coming back on is having been off >= brk minutes.
    - Rotate: most-played comes off for least-played (when there is a rested sub waiting).
    - At least `slots` players are on field at all times.
    """
    by_pos = {}
    for p in squad_players:
        pk = pos_map.get(p["id"])
        if pk:
            by_pos.setdefault(pk, []).append(p)

    schedule = {}
    field_positions = [p2 for p2 in POS_ORDER if p2 != "GK"]

    for pk, slots in fmt.items():
        players = by_pos.get(pk, [])
        if not players:
            continue

        if pk == "GK":
            if len(players) == 1:
                schedule[players[0]["id"]] = [(0, total)]
            else:
                sl = total // len(players)
                for i, p in enumerate(players):
                    start = i * sl
                    end = total if i == len(players) - 1 else (i + 1) * sl
                    schedule[p["id"]] = [(start, end)]
            continue

        # ── Max continuous stint: evenly caps how long anyone stays on ─────────
        # e.g. with 3 players for 2 slots over 60 min: fair = 40 min each
        # max_stint caps any single continuous run to ~2/3 of fair share,
        # floored at qm so we never create chaos with tiny forced subs.
        n = len(players)
        fair_mins = round((slots / n) * total) if n > slots else total
        max_stint = max(qm, min(qm * 2, round(fair_mins * 0.7)))

        # Stagger each position group so different groups never all sub together
        group_idx   = field_positions.index(pk)
        base_offset = (group_idx * brk) % qm

        sub_times = set([0, total])
        for q in range(4):
            qs = q * qm
            t  = qs + base_offset
            if t == qs:
                t += brk
            while t < (q + 1) * qm:
                sub_times.add(t)
                t += brk
        sub_times = sorted(sub_times)
        intervals  = list(zip(sub_times[:-1], sub_times[1:]))

        mins_on     = {p["id"]: 0          for p in players}
        last_off    = {p["id"]: -(brk + 1) for p in players}
        stint_start = {p["id"]: None        for p in players}

        for p in players:
            schedule[p["id"]] = []

        on_field = [p["id"] for p in players[:min(slots, len(players))]]
        for pid in on_field:
            stint_start[pid] = 0

        for start, end in intervals:
            dur = end - start

            # Who has been on continuously too long?
            must_off = [
                pid for pid in on_field
                if stint_start[pid] is not None
                and (start - stint_start[pid]) >= max_stint
            ]

            # Available bench players sorted by least total time played
            def rested_bench():
                return sorted(
                    [p for p in players if p["id"] not in on_field
                     and start - last_off[p["id"]] >= brk],
                    key=lambda p: mins_on[p["id"]]
                )

            # Force off anyone who has hit max_stint
            bench = rested_bench()
            for pid_out in must_off:
                if not bench:
                    break
                p_in = bench.pop(0)
                on_field.remove(pid_out)
                last_off[pid_out]    = start
                stint_start[pid_out] = None
                on_field.append(p_in["id"])
                stint_start[p_in["id"]] = start

            # Voluntary fairness swap: most-played off for least-played bench
            bench = rested_bench()
            if bench:
                best_in  = bench[0]
                best_out = max(
                    (p for p in players if p["id"] in on_field),
                    key=lambda p: mins_on[p["id"]]
                )
                outgoing_stint = start - (stint_start[best_out["id"]] or start)
                if (mins_on[best_out["id"]] > mins_on[best_in["id"]]
                        and outgoing_stint >= brk):
                    on_field.remove(best_out["id"])
                    last_off[best_out["id"]]    = start
                    stint_start[best_out["id"]] = None
                    on_field.append(best_in["id"])
                    stint_start[best_in["id"]] = start

            # Safety fill — keep the field at the right number
            for p in rested_bench():
                if len(on_field) >= slots:
                    break
                on_field.append(p["id"])
                stint_start[p["id"]] = start

            # Accumulate time and build/extend segments
            for pid in on_field:
                mins_on[pid] += dur
                segs = schedule[pid]
                if segs and segs[-1][1] == start:
                    schedule[pid][-1] = (segs[-1][0], end)
                else:
                    schedule[pid].append((start, end))

    return schedule

def compute_sub_for(schedule, squad_players, pos_map):
    result = {}
    for p in squad_players:
        result[p["id"]] = {}
        segs = schedule.get(p["id"], [])
        for seg_start, _ in segs:
            if seg_start == 0:
                continue
            same_pos = [o for o in squad_players if o["id"] != p["id"] and pos_map.get(o["id"]) == pos_map.get(p["id"])]
            for other in same_pos:
                other_segs = schedule.get(other["id"], [])
                was_on = any(s <= seg_start - 1 < e for s, e in other_segs)
                is_on  = any(s <= seg_start < e    for s, e in other_segs)
                if was_on and not is_on:
                    result[p["id"]][seg_start] = ini(other["name"])
                    break
    return result

def playing_time(schedule, pid, total):
    segs = schedule.get(pid, [])
    return sum(e - s for s, e in segs)

def on_field_at(schedule, squad_players, minute):
    return sum(
        1 for p in squad_players
        if any(s <= minute < e for s, e in schedule.get(p["id"], []))
    )

# ── session state init ───────────────────────────────────────────────────────

def ss(key, default):
    if key not in st.session_state:
        st.session_state[key] = default

ss("roster", [])
ss("squad", [])
ss("pos_map", {})
ss("schedule", {})
ss("qm", 15)
ss("brk", 5)
ss("formation_name", "4-3-3")
ss("custom_fmt", {"GK": 1, "CD": 2, "WD": 2, "MID": 3, "ATT": 3})
ss("tab", "squad")
ss("match_tab", "setup")
ss("dark_mode", False)
ss("editing_pid", None)
ss("ls_import", None)   # holds JSON string arriving from localStorage

# ── theme: defer to Streamlit native light/dark ──────────────────────────────
# Use CSS variables so inline HTML adapts automatically to both modes.
TEXT     = "var(--text-color)"
TEXT_MUT = "var(--text-color-secondary, #64748b)"
BG       = "var(--background-color)"
BG2      = "var(--secondary-background-color)"
BORDER   = "var(--secondary-background-color)"
DIVIDER  = "var(--secondary-background-color)"
CARD_BG  = "var(--secondary-background-color)"
HEADER_BG= "var(--secondary-background-color)"
INPUT_BG = "var(--secondary-background-color)"
BAR_BG   = "var(--secondary-background-color)"
Q_DIV    = "#86c68d"
TICK_MUT = "var(--text-color-secondary, #94a3b8)"

st.markdown("""
<style>
  /* Layout */
  .block-container { padding-top: 3.5rem !important; }
  /* Buttons — monospace font only */
  button[kind="primary"], button[kind="secondary"],
  button[kind="secondaryFormSubmit"] {
    font-family: monospace !important;
    font-size: 12px !important;
  }
  /* Spacing */
  div[data-testid="stHorizontalBlock"] { gap: 4px; }
  /* Headings */
  h1, h2, h3 { font-family: monospace !important; }
  @media print {
    .no-print { display: none !important; }
    .stApp { background: white !important; }
    header, footer, [data-testid="stToolbar"] { display: none !important; }
  }
</style>
""", unsafe_allow_html=True)

# ── derived state ─────────────────────────────────────────────────────────────

fmt = (st.session_state.custom_fmt
       if st.session_state.formation_name == "Custom"
       else FORMATIONS[st.session_state.formation_name])
total        = st.session_state.qm * 4
qm           = st.session_state.qm
brk          = st.session_state.brk
squad_ids    = st.session_state.squad
squad_players = [
    {**p, "pos": st.session_state.pos_map.get(p["id"])}
    for p in st.session_state.roster
    if p["id"] in squad_ids
]
schedule  = st.session_state.schedule
sub_for   = compute_sub_for(schedule, squad_players, st.session_state.pos_map)
tgt       = sum(fmt.values())

# ── header ────────────────────────────────────────────────────────────────────

st.markdown(
    f'<div style="font-family:monospace;font-size:22px;font-weight:700;'
    f'color:{TEXT};padding-top:4px;margin-bottom:8px">🏑 HOCKEY MANAGER</div>',
    unsafe_allow_html=True
)

tab_col1, tab_col2, tab_col3, _ = st.columns([1, 1, 1, 7])
with tab_col1:
    if st.button("👥 Squad", use_container_width=True,
                 type="primary" if st.session_state.tab == "squad" else "secondary"):
        st.session_state.tab = "squad"
        st.rerun()
with tab_col2:
    if st.button("⚽ Match", use_container_width=True,
                 type="primary" if st.session_state.tab == "match" else "secondary"):
        st.session_state.tab = "match"
        st.rerun()
with tab_col3:
    if st.button("📖 Guide", use_container_width=True,
                 type="primary" if st.session_state.tab == "guide" else "secondary"):
        st.session_state.tab = "guide"
        st.rerun()

st.divider()

# ── Team save / load (localStorage) ──────────────────────────────────────────
# We use a small HTML component to read/write localStorage.
# Streamlit can't directly read component output, so we use a URL query-param
# trick: the component writes the team JSON to a hidden text area that Streamlit
# reads via session_state on the next rerun via st.query_params.

with st.expander("💾 Save / Load Team", expanded=False):
    save_col, load_col = st.columns([1, 1])

    with save_col:
        st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:2px;color:{TEXT_MUT}">SAVE CURRENT ROSTER</div>', unsafe_allow_html=True)
        team_name_save = st.text_input("Team name", placeholder="e.g. U16 Greens", key="team_name_save", label_visibility="collapsed")
        if st.button("💾 Save to this browser", use_container_width=True):
            if team_name_save.strip():
                payload = json.dumps({
                    "roster":  st.session_state.roster,
                    "pos_map": st.session_state.pos_map,
                })
                safe_name = team_name_save.strip().replace('"', "'")
                safe_payload = payload.replace("`", "'").replace("\\", "\\\\")
                st.components.v1.html(f"""
<script>
  localStorage.setItem('hm_team_{{}}'.replace('{{}}', '{safe_name}'), `{safe_payload}`);
  // Show saved teams list
  var keys = [];
  for(var i=0;i<localStorage.length;i++){{
    if(localStorage.key(i).startsWith('hm_team_')) keys.push(localStorage.key(i).replace('hm_team_',''));
  }}
  document.getElementById('saved-list').innerText = 'Saved: ' + keys.join(', ');
</script>
<div id="saved-list" style="font-family:monospace;font-size:11px;color:#16a34a;margin-top:4px">Saving…</div>
""", height=40)
                st.success(f"Saved '{team_name_save.strip()}' to this browser's storage.")

    with load_col:
        st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:2px;color:{TEXT_MUT}">LOAD SAVED ROSTER</div>', unsafe_allow_html=True)
        team_name_load = st.text_input("Load team name", placeholder="e.g. U16 Greens", key="team_name_load", label_visibility="collapsed")

        # Component that reads from localStorage and writes to a query param
        _safe_load_name = team_name_load.strip().replace("'", "\\'")
        ls_result = st.components.v1.html(f"""
<script>
  var name = '{_safe_load_name}';
  if(name) {{
    var data = localStorage.getItem('hm_team_' + name);
    if(data) {{
      document.getElementById('ls-out').value = data;
      document.getElementById('ls-status').innerText = '✓ Found — press Load below';
      document.getElementById('ls-status').style.color = '#16a34a';
    }} else {{
      // Show available teams
      var keys = [];
      for(var i=0;i<localStorage.length;i++){{
        if(localStorage.key(i).startsWith('hm_team_')) keys.push(localStorage.key(i).replace('hm_team_',''));
      }}
      document.getElementById('ls-status').innerText = keys.length ? 'Not found. Saved teams: ' + keys.join(', ') : 'No saved teams yet.';
      document.getElementById('ls-status').style.color = '#d97706';
    }}
  }}
</script>
<textarea id="ls-out" style="display:none"></textarea>
<div id="ls-status" style="font-family:monospace;font-size:11px;color:{TEXT_MUT}">Type a team name above</div>
""", height=30)

        load_json = st.text_area(
            "Paste saved team JSON here (copy from another device)",
            height=80,
            placeholder='{"roster":[...],"pos_map":{...}}',
            key="load_json_input",
            label_visibility="collapsed",
            help="On the same browser: just type the name above and click Load. On a different device, copy the JSON from the Save tab and paste it here."
        )
        if st.button("📂 Load Team", use_container_width=True):
            raw = load_json.strip()
            if raw:
                try:
                    data = json.loads(raw)
                    st.session_state.roster  = data.get("roster", [])
                    st.session_state.pos_map = data.get("pos_map", {})
                    st.session_state.squad   = []
                    st.session_state.schedule = {}
                    st.success("Team loaded! Re-select your match squad in the Match tab.")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Could not parse JSON: {ex}")
            else:
                st.warning("Paste the team JSON in the box above, or use the same browser where you saved it.")

        # Also show the current roster as exportable JSON for copy-paste
        if st.session_state.roster:
            export = json.dumps({"roster": st.session_state.roster, "pos_map": st.session_state.pos_map}, indent=2)
            st.text_area("📋 Copy this to save/share your team", value=export, height=100, key="export_json", label_visibility="visible")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SQUAD TAB
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.tab == "squad":
    st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:3px;color:{TEXT_MUT};margin-bottom:8px">SQUAD MANAGEMENT</div>', unsafe_allow_html=True)

    col_add, col_test = st.columns([5, 1])
    with col_add:
        c1, c2, c3 = st.columns([1, 4, 1])
        with c1:
            new_num = st.text_input("Number", placeholder="#", label_visibility="collapsed", key="new_num")
        with c2:
            new_name = st.text_input("Name", placeholder="Player name…", label_visibility="collapsed", key="new_name")
        with c3:
            if st.button("➕ Add", use_container_width=True):
                if new_name.strip():
                    st.session_state.roster.append({
                        "id": f"p{len(st.session_state.roster)+1}_{new_name[:6]}",
                        "number": new_num.strip() or "—",
                        "name": new_name.strip(),
                    })
                    st.rerun()
    with col_test:
        if st.button("🧪 Load Test", use_container_width=True):
            existing = {p["name"] for p in st.session_state.roster}
            for num, name in TEST_NAMES:
                if name not in existing:
                    st.session_state.roster.append({
                        "id": f"t{num}",
                        "number": num,
                        "name": name,
                    })
            st.rerun()

    st.markdown(f'<span style="color:{TEXT};font-family:monospace"><b>{len(st.session_state.roster)} players</b></span>', unsafe_allow_html=True)
    st.divider()

    if not st.session_state.roster:
        st.info("No players yet — add some above or load the test squad.")
    else:
        cols = st.columns(3)
        for i, p in enumerate(st.session_state.roster):
            pc = POS[st.session_state.pos_map.get(p["id"], "")]["color"] if st.session_state.pos_map.get(p["id"]) else "#22c55e"
            with cols[i % 3]:
                c1, c2 = st.columns([5, 1])
                with c1:
                    pos_label = st.session_state.pos_map.get(p["id"], "")
                    st.markdown(
                        f'<div style="padding:6px 10px;border-radius:6px;margin-bottom:3px;'
                        f'font-family:monospace;font-size:13px;display:flex;align-items:center;gap:10px;'
                        f'background:{CARD_BG};border-left:3px solid {pc}">'
                        f'<span style="color:{pc};font-weight:700;width:28px">#{p["number"]}</span>'
                        f'<span style="flex:1;color:{TEXT}">{p["name"]}</span>'
                        f'<span style="color:{pc};font-size:11px">{pos_label}</span>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                with c2:
                    if st.button("✕", key=f"rm_{p['id']}"):
                        st.session_state.roster = [r for r in st.session_state.roster if r["id"] != p["id"]]
                        st.session_state.squad  = [s for s in st.session_state.squad  if s != p["id"]]
                        st.session_state.pos_map.pop(p["id"], None)
                        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# MATCH TAB
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.tab == "match":
    mt1, mt2, _ = st.columns([1, 1, 8])
    with mt1:
        if st.button("⚙ Setup", use_container_width=True,
                     type="primary" if st.session_state.match_tab == "setup" else "secondary"):
            st.session_state.match_tab = "setup"
            st.rerun()
    with mt2:
        if st.button("📋 Sub Sheet", use_container_width=True,
                     type="primary" if st.session_state.match_tab == "sub" else "secondary"):
            st.session_state.match_tab = "sub"
            st.rerun()

    st.divider()

    # ── SETUP ─────────────────────────────────────────────────────────────────
    if st.session_state.match_tab == "setup":

        left, right = st.columns([1, 1])

        with left:
            st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:3px;color:{TEXT_MUT};margin-bottom:8px;margin-top:16px">QUARTER DURATION</div>', unsafe_allow_html=True)
            qm_cols = st.columns(5)
            for i, m in enumerate([10, 12, 15, 20]):
                with qm_cols[i]:
                    if st.button(f"{m}m", key=f"qm_{m}", use_container_width=True,
                                 type="primary" if st.session_state.qm == m else "secondary"):
                        st.session_state.qm = m
                        st.rerun()
            with qm_cols[4]:
                new_qm = st.number_input("Custom minutes", min_value=1, max_value=45,
                                          value=st.session_state.qm,
                                          label_visibility="collapsed", key="qm_custom")
                if new_qm != st.session_state.qm:
                    st.session_state.qm = new_qm
                    st.rerun()
            st.caption(f"4 × {st.session_state.qm}min = **{st.session_state.qm*4}min total**")

            st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:3px;color:{TEXT_MUT};margin-bottom:8px;margin-top:16px">MIN BREAK (GKs EXEMPT)</div>', unsafe_allow_html=True)
            brk_cols = st.columns(6)
            for i, m in enumerate([2, 3, 5, 7, 10]):
                with brk_cols[i]:
                    if st.button(f"{m}m", key=f"brk_{m}", use_container_width=True,
                                 type="primary" if st.session_state.brk == m else "secondary"):
                        st.session_state.brk = m
                        st.rerun()
            with brk_cols[5]:
                new_brk = st.number_input("Custom break", min_value=1, max_value=20,
                                           value=st.session_state.brk,
                                           label_visibility="collapsed", key="brk_custom")
                if new_brk != st.session_state.brk:
                    st.session_state.brk = new_brk
                    st.rerun()

            st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:3px;color:{TEXT_MUT};margin-bottom:8px;margin-top:16px">FORMATION</div>', unsafe_allow_html=True)
            fn_options = list(FORMATIONS.keys()) + ["Custom"]
            new_fn = st.radio("Formation", fn_options, horizontal=True,
                               index=fn_options.index(st.session_state.formation_name),
                               label_visibility="collapsed")
            if new_fn != st.session_state.formation_name:
                st.session_state.formation_name = new_fn
                st.rerun()

            if st.session_state.formation_name == "Custom":
                cust_cols = st.columns(5)
                cust = st.session_state.custom_fmt.copy()
                changed = False
                for i, pk in enumerate(POS_ORDER):
                    with cust_cols[i]:
                        st.caption(pk)
                        v = st.number_input("Count", min_value=0, max_value=7,
                                             value=cust.get(pk, 0),
                                             key=f"cust_{pk}",
                                             label_visibility="collapsed")
                        if v != cust.get(pk, 0):
                            cust[pk] = v
                            changed = True
                if changed:
                    st.session_state.custom_fmt = cust
                    st.rerun()

            tile_cols = st.columns(5)
            for i, pk in enumerate(POS_ORDER):
                with tile_cols[i]:
                    v = fmt.get(pk, 0)
                    c = POS[pk]["color"]
                    st.markdown(
                        f'<div style="text-align:center;background:{c}18;border:1px solid {c}44;border-radius:6px;padding:6px 2px">'
                        f'<div style="font-size:22px;font-weight:700;color:{c}">{v}</div>'
                        f'<div style="font-size:9px;color:{c};font-family:monospace">{pk}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            st.caption(f"Total on field: **{tgt}**")

        with right:
            st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:3px;color:{TEXT_MUT};margin-bottom:8px;margin-top:16px">MATCH SQUAD</div>', unsafe_allow_html=True)
            n_squad = len(st.session_state.squad)
            squad_color = "#16a34a" if n_squad == 18 else "#d97706"
            st.markdown(f'<span style="color:{squad_color};font-family:monospace;font-weight:700">{n_squad}/18 selected</span>', unsafe_allow_html=True)

            if not st.session_state.roster:
                st.info("Add players in the Squad tab first.")
            else:
                for p in st.session_state.roster:
                    in_squad = p["id"] in st.session_state.squad
                    ppos = st.session_state.pos_map.get(p["id"], "")
                    pc = POS[ppos]["color"] if ppos else "#334155"

                    rc1, rc2, rc3 = st.columns([3, 2, 1])
                    with rc1:
                        checked = st.checkbox(
                            f"#{p['number']}  {p['name']}",
                            value=in_squad,
                            key=f"squad_{p['id']}"
                        )
                        if checked != in_squad:
                            if checked and len(st.session_state.squad) < 18:
                                st.session_state.squad.append(p["id"])
                            elif not checked:
                                st.session_state.squad = [s for s in st.session_state.squad if s != p["id"]]
                                st.session_state.pos_map.pop(p["id"], None)
                            st.rerun()
                    with rc2:
                        if in_squad:
                            pos_options = [""] + POS_ORDER
                            pos_labels  = ["Position…"] + [f"{k} — {POS[k]['label']}" for k in POS_ORDER]
                            cur_idx = pos_options.index(ppos) if ppos in pos_options else 0
                            sel = st.selectbox("Position", pos_options, index=cur_idx,
                                                format_func=lambda x: pos_labels[pos_options.index(x)],
                                                key=f"pos_{p['id']}",
                                                label_visibility="collapsed")
                            if sel != ppos:
                                if sel:
                                    st.session_state.pos_map[p["id"]] = sel
                                else:
                                    st.session_state.pos_map.pop(p["id"], None)
                                st.rerun()
                    with rc3:
                        if in_squad and ppos:
                            st.markdown(
                                f'<span style="display:inline-block;padding:2px 8px;border-radius:12px;'
                                f'font-size:11px;font-family:monospace;font-weight:700;'
                                f'background:{pc}22;color:{pc};border:1px solid {pc}55">{ppos}</span>',
                                unsafe_allow_html=True
                            )

                no_pos = [p for p in squad_players if not p.get("pos")]
                if no_pos:
                    st.warning(f"⚠ {len(no_pos)} squad players without a position assigned.")

    # ── SUB SHEET ──────────────────────────────────────────────────────────────
    elif st.session_state.match_tab == "sub":

        st.markdown(
            f'<div style="font-family:monospace;font-size:11px;color:{TEXT_MUT};margin-bottom:8px">'
            f'{total}min · <span style="color:#7c3aed">{brk}min break</span> · '
            f'<span style="color:#2563eb">{tgt} on field</span> · '
            f'times shown as scoreboard countdown</div>',
            unsafe_allow_html=True
        )

        # Action buttons
        btn1, btn2, btn3, btn4, btn5, _ = st.columns([1.2, 1.5, 1, 1.2, 1.2, 3])
        with btn1:
            if st.button("⚡ Auto Generate", use_container_width=True, type="primary"):
                if squad_players:
                    new_sched = auto_generate(squad_players, st.session_state.pos_map, total, brk, qm, fmt)
                    st.session_state.schedule.update(new_sched)
                    st.rerun()
        with btn2:
            if st.button("🤖 AI Generate", use_container_width=True):
                st.session_state.match_tab = "ai"
                st.rerun()
        with btn3:
            if st.button("🗑 Clear", use_container_width=True):
                st.session_state.schedule = {}
                st.rerun()
        with btn4:
            if st.button("✏️ Edit Times", use_container_width=True,
                         type="primary" if st.session_state.get("show_editor") else "secondary"):
                st.session_state.show_editor = not st.session_state.get("show_editor", False)
                st.rerun()
        with btn5:
            if st.button("🖨️ Print Sheet", use_container_width=True):
                st.session_state.show_print = not st.session_state.get("show_print", False)
                st.rerun()

        # ── Inline segment editor ───────────────────────────────────────────
        if st.session_state.get("show_editor", False) and squad_players:
            st.markdown(
                f'<div style="background:{BG2};border:1px solid {BORDER};border-radius:8px;padding:12px;margin-bottom:12px">',
                unsafe_allow_html=True
            )
            st.markdown(
                f'<div style="font-family:monospace;font-size:11px;font-weight:700;color:{TEXT};margin-bottom:6px">'
                f'✏️ EDIT PLAYING TIMES — enter segments as start-end pairs, e.g. <code style="background:{BAR_BG};padding:1px 4px;border-radius:3px">0-30, 40-60</code></div>',
                unsafe_allow_html=True
            )

            # Group by position for clarity
            for pk in POS_ORDER:
                pos_players_edit = [p for p in squad_players if p.get("pos") == pk]
                if not pos_players_edit:
                    continue
                pc = POS[pk]["color"]
                st.markdown(
                    f'<div style="font-family:monospace;font-size:9px;font-weight:700;color:{pc};'
                    f'letter-spacing:2px;margin-top:10px;margin-bottom:4px">{POS[pk]["label"].upper()}</div>',
                    unsafe_allow_html=True
                )
                for p in pos_players_edit:
                    pid  = p["id"]
                    segs = schedule.get(pid, [])
                    cur  = ", ".join(f"{s}-{e}" for s, e in segs)
                    pt   = playing_time(schedule, pid, total)
                    pt_color = "#16a34a" if pt == total else ("#e2e8f0" if pt > 0 else "#94a3b8")
                    if not dark:
                        pt_color = "#16a34a" if pt == total else ("#374151" if pt > 0 else "#94a3b8")

                    c_name, c_input, c_info, c_all, c_clr = st.columns([2, 5, 1, 0.7, 0.7])
                    with c_name:
                        st.markdown(
                            f'<div style="font-family:monospace;font-size:12px;color:{pc};font-weight:700;'
                            f'padding-top:6px">#{p["number"]} {p["name"].split()[0]}</div>',
                            unsafe_allow_html=True
                        )
                    with c_input:
                        val = st.text_input(
                            "", value=cur, key=f"seg_{pid}",
                            label_visibility="collapsed",
                            placeholder=f"e.g. 0-{total//2}, {total//2}-{total}"
                        )
                        if val != cur:
                            new_segs = []
                            try:
                                for part in val.split(","):
                                    part = part.strip()
                                    if "-" in part:
                                        a, b = part.split("-", 1)
                                        new_segs.append((int(a.strip()), int(b.strip())))
                                schedule[pid] = merge_segs(new_segs)
                                st.session_state.schedule = schedule
                                st.rerun()
                            except Exception:
                                pass
                    with c_info:
                        st.markdown(
                            f'<div style="font-family:monospace;font-size:11px;font-weight:700;'
                            f'color:{pt_color};padding-top:6px">{pt}m</div>',
                            unsafe_allow_html=True
                        )
                    with c_all:
                        if st.button("all", key=f"all_e_{pid}", use_container_width=True):
                            schedule[pid] = [(0, total)]
                            st.session_state.schedule = schedule
                            st.rerun()
                    with c_clr:
                        if st.button("✕", key=f"clr_e_{pid}", use_container_width=True):
                            schedule[pid] = []
                            st.session_state.schedule = schedule
                            st.rerun()

            st.markdown('</div>', unsafe_allow_html=True)

        # ── Print sheet — opens popup window, auto-triggers print dialog ──────
        if st.session_state.get("show_print", False) and squad_players:
            # ── Build Gantt rows HTML for print ──────────────────────────────
            def gantt_row_html(p_obj, segs_list, pos_color, total_mins, qm_mins):
                pid_pt  = sum(e - s for s, e in segs_list)
                pid_pct = round(pid_pt / total_mins * 100) if total_mins else 0
                initials = ini(p_obj["name"])
                # bar segments as absolutely-positioned divs
                bar_segs = ""
                for s, e in segs_list:
                    lp = s / total_mins * 100
                    wp = (e - s) / total_mins * 100
                    lbl = f"{s}–{e}" if wp > 6 else ""
                    bar_segs += (
                        f'<div style="position:absolute;left:{lp:.2f}%;width:{wp:.2f}%;'
                        f'top:2px;bottom:2px;border-radius:3px;'
                        f'background:{pos_color};opacity:0.85;'
                        f'display:flex;align-items:center;justify-content:center;'
                        f'font-size:7px;color:#fff;font-weight:700;overflow:hidden">{lbl}</div>'
                    )
                # quarter dividers
                q_divs = ""
                for qi in range(1, 4):
                    lp = qi * qm_mins / total_mins * 100
                    q_divs += f'<div style="position:absolute;left:{lp:.2f}%;top:0;bottom:0;width:1px;background:#9ca3af;z-index:1"></div>'
                pt_col = "#16a34a" if pid_pct >= 95 else "#374151"
                return f"""
<tr>
  <td style="padding:2px 4px;font-weight:700;white-space:nowrap;color:{pos_color}">{initials}<br><span style="font-size:8px;color:#6b7280">#{p_obj['number']}</span></td>
  <td style="padding:2px 4px;white-space:nowrap;font-size:9px">{p_obj['name']}</td>
  <td style="padding:2px 4px;width:100%">
    <div style="position:relative;height:22px;background:#f1f5f9;border-radius:3px;border:1px solid #e2e8f0;overflow:hidden">
      {q_divs}{bar_segs}
    </div>
  </td>
  <td style="padding:2px 6px;text-align:right;font-weight:700;white-space:nowrap;color:{pt_col}">{pid_pt}m&nbsp;{pid_pct}%</td>
</tr>"""

            # Build quarter tick header for print gantt
            def gantt_header_html(total_mins, qm_mins):
                ticks = ""
                for qi in range(4):
                    lp = qi * qm_mins / total_mins * 100
                    ticks += f'<div style="position:absolute;left:{lp:.2f}%;transform:translateX(-50%);font-size:8px;font-weight:700;color:#374151">Q{qi+1}</div>'
                    # mid-quarter
                    mid = (qi + 0.5) * qm_mins / total_mins * 100
                    ticks += f'<div style="position:absolute;left:{mid:.2f}%;transform:translateX(-50%);font-size:7px;color:#9ca3af">{qm_mins//2}</div>'
                ticks += f'<div style="position:absolute;right:0;transform:translateX(50%);font-size:8px;font-weight:700;color:#374151">END</div>'
                return f'<div style="position:relative;height:18px;margin-bottom:2px">{ticks}</div>'

            # Build sub events table rows
            sub_events = []
            for pk_se in POS_ORDER:
                pos_players_sub = [p for p in squad_players if p.get("pos") == pk_se]
                if not pos_players_sub:
                    continue
                ev_map = {}
                for p in pos_players_sub:
                    for s, e in schedule.get(p["id"], []):
                        if s > 0:
                            ev_map.setdefault(s, {"on": [], "off": []})["on"].append(p)
                        if e < total:
                            ev_map.setdefault(e, {"on": [], "off": []})["off"].append(p)
                for m in sorted(ev_map.keys()):
                    q_ev  = m // qm
                    rem_ev = qm - (m % qm) if m % qm != 0 else qm
                    sub_events.append((m, q_ev, rem_ev, ev_map[m]["off"], ev_map[m]["on"], pk_se))
            sub_events.sort(key=lambda x: x[0])

            sub_rows_html = ""
            for idx, (m, q_ev, rem_ev, offs, ons, pk_se) in enumerate(sub_events):
                pc_se   = POS[pk_se]["color"]
                off_str = " · ".join(f"#{p['number']} {p['name']}" for p in offs) or "—"
                on_str  = " · ".join(f"#{p['number']} {p['name']}" for p in ons) or "—"
                rbg     = "#fff" if idx % 2 == 0 else "#f9fafb"
                sub_rows_html += f"""
<tr style="background:{rbg}">
  <td style="padding:2px 6px;font-weight:700">{m}min</td>
  <td style="padding:2px 6px">Q{q_ev+1} {rem_ev:02d}:00</td>
  <td style="padding:2px 6px;color:#dc2626">{off_str}</td>
  <td style="padding:2px 6px;color:#16a34a">{on_str}</td>
  <td style="padding:2px 6px;font-weight:700;color:{pc_se}">{pk_se}</td>
</tr>"""

            # Build full gantt section per position group
            gantt_section_html = ""
            for pk_g in POS_ORDER:
                grp = [p for p in squad_players if p.get("pos") == pk_g]
                if not grp:
                    continue
                pc_g = POS[pk_g]["color"]
                rows = "".join(gantt_row_html(p, schedule.get(p["id"], []), pc_g, total, qm) for p in grp)
                gantt_section_html += f"""
<tr>
  <td colspan="4" style="padding:3px 4px 1px;background:#f8fafc;
      font-size:9px;font-weight:700;letter-spacing:2px;color:{pc_g};
      border-top:1px solid #cbd5e1">
    {POS[pk_g]['label'].upper()}
  </td>
</tr>
{rows}"""

            # Full self-contained print document
            full_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Sub Sheet</title>
<style>
  @page {{ size: A4 landscape; margin: 10mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Courier New', monospace;
    font-size: 10px;
    color: #000;
    background: #fff;
    margin: 0;
    padding: 0;
  }}
  h1 {{ font-size: 15px; margin: 0 0 2px; }}
  .meta {{ font-size: 9px; color: #555; margin-bottom: 8px; }}
  .two-col {{ display: flex; gap: 16px; align-items: flex-start; }}
  .gantt-wrap {{ flex: 2; }}
  .subs-wrap {{ flex: 1; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 9px; }}
  th {{ background: #f1f5f9; padding: 2px 6px; text-align: left; border-bottom: 1.5px solid #374151; }}
  td {{ vertical-align: middle; }}
  .section-label {{ font-size: 8px; font-weight: 700; letter-spacing: 2px; }}
  .footer {{ font-size: 8px; color: #9ca3af; text-align: center; margin-top: 8px; }}
  @media print {{
    body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  }}
</style>
</head>
<body>
<h1>🏑 FIELD HOCKEY — SUBSTITUTION SHEET</h1>
<div class="meta">{total}min match &nbsp;·&nbsp; {qm}min quarters &nbsp;·&nbsp; {tgt} on field &nbsp;·&nbsp; {brk}min min break</div>

<div class="two-col">

  <!-- LEFT: Gantt chart -->
  <div class="gantt-wrap">
    <div class="section-label" style="margin-bottom:4px;color:#374151">PLAYING TIME GANTT</div>
    {gantt_header_html(total, qm)}
    <table>
      <colgroup>
        <col style="width:30px">
        <col style="width:90px">
        <col>
        <col style="width:55px">
      </colgroup>
      <thead>
        <tr>
          <th></th>
          <th>Player</th>
          <th>
            <!-- tick ruler inside header cell -->
            <div style="position:relative;height:14px;">
              {''.join(f'<div style="position:absolute;left:{q*qm/total*100:.1f}%;font-size:7px;color:#6b7280;transform:translateX(-50%)">Q{q+1}</div>' for q in range(4))}
              {''.join(f'<div style="position:absolute;left:{(q*qm+qm/2)/total*100:.1f}%;font-size:6px;color:#9ca3af;transform:translateX(-50%)">{qm//2}</div>' for q in range(4))}
            </div>
          </th>
          <th style="text-align:right">Mins %</th>
        </tr>
      </thead>
      <tbody>
        {gantt_section_html}
      </tbody>
    </table>
  </div>

  <!-- RIGHT: Sub events -->
  <div class="subs-wrap">
    <div class="section-label" style="margin-bottom:4px;color:#374151">SUBSTITUTION EVENTS</div>
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Clock</th>
          <th style="color:#dc2626">OFF ▼</th>
          <th style="color:#16a34a">ON ▲</th>
          <th>Pos</th>
        </tr>
      </thead>
      <tbody>
        {sub_rows_html if sub_rows_html else '<tr><td colspan="5" style="padding:4px;color:#9ca3af">No substitutions scheduled</td></tr>'}
      </tbody>
    </table>
  </div>

</div>
<div class="footer">Generated by Field Hockey Manager</div>
<script>window.onload = function(){{ window.print(); }};</script>
</body>
</html>"""

            # Escape for JS string embedding
            escaped = full_doc.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")

            st.components.v1.html(f"""
<button onclick="
  var w = window.open('', '_blank', 'width=1100,height=750,scrollbars=yes');
  w.document.open();
  w.document.write(`{escaped}`);
  w.document.close();
" style="
  font-family:monospace;font-size:13px;font-weight:700;
  padding:8px 20px;border-radius:6px;cursor:pointer;
  background:#2563eb;color:#fff;border:none;
">🖨️ Open Print Preview &amp; Print</button>
<div style="font-family:monospace;font-size:11px;color:#64748b;margin-top:6px">
  Opens in a new window · landscape A4 · print dialog launches automatically
</div>
""", height=70)

        if not squad_players:
            st.info("Add match squad in Setup first.")
        else:
            st.divider()

            # ── Scrollable gantt wrapper ─────────────────────────────────────
            GANTT_W = 1400   # px — wide enough to see all bars clearly
            st.markdown(
                f'<div id="gantt-scroll" style="overflow-x:auto;overflow-y:visible;'
                f'border:1px solid {BORDER};border-radius:6px;padding:8px 12px;'
                f'background:{BG2}">',
                unsafe_allow_html=True
            )
            st.markdown(
                f'<div style="min-width:{GANTT_W}px">',
                unsafe_allow_html=True
            )

            # ── Timeline header ──────────────────────────────────────────────
            tick_marks = []
            for q in range(4):
                qs = q * qm
                tick_marks.append((qs, f"Q{q+1}", True))
                t = qs + 5
                while t < (q + 1) * qm:
                    rem = qm - (t % qm)
                    tick_marks.append((t, str(rem), False))
                    t += 5
            tick_marks.append((total, "0", True))

            header_html = f'<div style="font-family:monospace;font-size:9px;position:relative;height:32px;background:{HEADER_BG};border-radius:4px;margin-bottom:2px;border:1px solid {BORDER}">'
            for m, label, is_q in tick_marks:
                pct_pos = m / total * 100
                color = "#16a34a" if is_q else TICK_MUT
                fw = "700" if is_q else "400"
                header_html += (
                    f'<span style="position:absolute;left:{pct_pos}%;transform:translateX(-50%);'
                    f'top:4px;color:{color};font-weight:{fw};font-size:{9 if is_q else 8}px">{label}</span>'
                )
            header_html += '</div>'
            st.markdown(header_html, unsafe_allow_html=True)

            # ── Per-position groups ──────────────────────────────────────────
            for pk in POS_ORDER:
                pos_players = [p for p in squad_players if p.get("pos") == pk]
                if not pos_players:
                    continue

                pc = POS[pk]["color"]
                st.markdown(
                    f'<div style="font-family:monospace;font-size:9px;font-weight:700;'
                    f'color:{pc};letter-spacing:2px;margin-top:12px;margin-bottom:4px">'
                    f'■ {POS[pk]["label"].upper()}</div>',
                    unsafe_allow_html=True
                )

                for p in pos_players:
                    pid = p["id"]
                    segs = schedule.get(pid, [])
                    pt   = playing_time(schedule, pid, total)
                    pct  = round(pt / total * 100) if total else 0

                    sub_labels = {}
                    for s, _ in segs:
                        lbl = sub_for.get(pid, {}).get(s, "")
                        if lbl:
                            sub_labels[s] = lbl

                    col_badge, col_bar, col_info = st.columns([1, 8, 2])

                    with col_badge:
                        st.markdown(
                            f'<div style="width:40px;height:40px;border-radius:5px;'
                            f'background:{pc}18;border:1.5px solid {pc};'
                            f'display:flex;flex-direction:column;align-items:center;'
                            f'justify-content:center;font-family:monospace">'
                            f'<div style="font-size:10px;font-weight:700;color:{pc};line-height:1.2">{ini(p["name"])}</div>'
                            f'<div style="font-size:7px;color:{pc};opacity:0.7">#{p["number"]}</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

                    with col_bar:
                        bar_html = f'<div style="position:relative;height:44px;margin-top:12px;background:{BAR_BG};border-radius:4px;border:1px solid {BORDER};overflow:visible">'
                        for q in range(1, 4):
                            lp = q * qm / total * 100
                            bar_html += f'<div style="position:absolute;left:{lp}%;top:0;bottom:0;width:1px;background:{Q_DIV};z-index:1"></div>'
                        for s, e in segs:
                            lp  = s / total * 100
                            wp  = (e - s) / total * 100
                            wide = wp > 10  # enough room for text inside
                            inner_lbl = f"{s}–{e}" if wide else ""
                            bar_html += (
                                f'<div style="position:absolute;left:{lp}%;width:{wp}%;top:4px;height:36px;'
                                f'border-radius:3px;background:linear-gradient(90deg,{pc}bb,{pc});'
                                f'z-index:2;display:flex;align-items:center;justify-content:center;'
                                f'font-size:10px;color:#fff;font-weight:700;font-family:monospace;overflow:hidden">'
                                f'{inner_lbl}</div>'
                            )
                            # For narrow segments, show label above the bar
                            if not wide:
                                bar_html += (
                                    f'<div style="position:absolute;left:{lp + wp/2}%;transform:translateX(-50%);'
                                    f'top:-18px;font-size:9px;color:{pc};font-weight:700;font-family:monospace;'
                                    f'white-space:nowrap;background:{BAR_BG};padding:0 2px;'
                                    f'border-radius:2px;border:1px solid {pc}44;z-index:6">'
                                    f'{s}–{e}</div>'
                                )
                        for s, lbl in sub_labels.items():
                            lp = s / total * 100
                            q_idx = s // qm
                            rem = qm - (s % qm) if s % qm != 0 else qm
                            bar_html += (
                                f'<div style="position:absolute;left:{lp}%;bottom:-18px;'
                                f'font-size:8px;color:{pc};white-space:nowrap;font-family:monospace;'
                                f'background:{BAR_BG};padding:0 2px;border-radius:2px;border:1px solid {pc}44;z-index:5">'
                                f'↑{lbl} Q{q_idx+1}:{rem:02d}</div>'
                            )
                        bar_html += '</div>'
                        st.markdown(bar_html, unsafe_allow_html=True)

                    with col_info:
                        pt_color = "#16a34a" if pt == total else (TEXT if pt > 0 else TEXT_MUT)
                        is_editing = st.session_state.editing_pid == pid
                        edit_label = f"{'▲' if is_editing else '▼'} {pt}m {pct}%"
                        if st.button(edit_label, key=f"tog_{pid}", use_container_width=True):
                            st.session_state.editing_pid = None if is_editing else pid
                            st.rerun()
                        qa, qb = st.columns(2)
                        with qa:
                            if st.button("all", key=f"all_{pid}"):
                                schedule[pid] = [(0, total)]
                                st.session_state.schedule = schedule
                                st.session_state.editing_pid = None
                                st.rerun()
                        with qb:
                            if segs and st.button("✕", key=f"clr_{pid}"):
                                schedule[pid] = []
                                st.session_state.schedule = schedule
                                st.session_state.editing_pid = None
                                st.rerun()

                    # ── Per-segment editor: number inputs for start & end ─────
                    if st.session_state.editing_pid == pid:
                        segs_now = list(schedule.get(pid, []))
                        if not segs_now:
                            st.markdown(
                                f'<div style="font-family:monospace;font-size:11px;color:{TEXT_MUT};'
                                f'padding:6px 0 6px 0">No segments yet — press <b>all</b> to set full match, '
                                f'or use Auto Generate.</div>',
                                unsafe_allow_html=True
                            )
                        else:
                            st.markdown(
                                f'<div style="font-family:monospace;font-size:9px;color:{TEXT_MUT};'
                                f'letter-spacing:1px;margin-bottom:2px">'
                                f'EDIT SEGMENTS FOR #{p["number"]} {p["name"]} '
                                f'<span style="color:{pc}">{pk}</span></div>',
                                unsafe_allow_html=True
                            )
                            changed = False
                            new_segs = list(segs_now)
                            for si, (seg_s, seg_e) in enumerate(segs_now):
                                sc_lbl, sc_s, sc_e, sc_dur, sc_del = st.columns([1.2, 2, 2, 1.2, 0.8])
                                with sc_lbl:
                                    st.markdown(
                                        f'<div style="font-family:monospace;font-size:11px;'
                                        f'color:{pc};font-weight:700;padding-top:28px">Seg {si+1}</div>',
                                        unsafe_allow_html=True
                                    )
                                with sc_s:
                                    new_s = st.number_input(
                                        f"On (min)", min_value=0, max_value=total-1,
                                        value=seg_s, step=1,
                                        key=f"ni_s_{pid}_{si}"
                                    )
                                with sc_e:
                                    new_e = st.number_input(
                                        f"Off (min)", min_value=1, max_value=total,
                                        value=seg_e, step=1,
                                        key=f"ni_e_{pid}_{si}"
                                    )
                                with sc_dur:
                                    dur_val = max(0, new_e - new_s)
                                    col_d = "#16a34a" if dur_val >= brk else "#d97706"
                                    st.markdown(
                                        f'<div style="font-family:monospace;font-size:11px;'
                                        f'color:{col_d};padding-top:28px">{dur_val}min</div>',
                                        unsafe_allow_html=True
                                    )
                                with sc_del:
                                    st.markdown('<div style="padding-top:20px">', unsafe_allow_html=True)
                                    if st.button("✕", key=f"del_seg_{pid}_{si}"):
                                        new_segs.pop(si)
                                        schedule[pid] = merge_segs(new_segs)
                                        st.session_state.schedule = schedule
                                        st.rerun()
                                    st.markdown('</div>', unsafe_allow_html=True)
                                if new_s != seg_s or new_e != seg_e:
                                    new_segs[si] = (int(new_s), int(new_e))
                                    changed = True
                            if changed:
                                valid = [(s, e) for s, e in new_segs if e > s]
                                schedule[pid] = merge_segs(valid)
                                st.session_state.schedule = schedule
                                st.rerun()
                            # Add segment button
                            if st.button(f"＋ Add segment", key=f"add_seg_{pid}"):
                                last_e = segs_now[-1][1] if segs_now else 0
                                if last_e < total:
                                    new_start = min(last_e + brk, total - 1)
                                    new_end   = min(new_start + qm, total)
                                    segs_now.append((new_start, new_end))
                                    schedule[pid] = merge_segs(segs_now)
                                    st.session_state.schedule = schedule
                                    st.rerun()

                # Sub rotation chips
                all_events = []
                for p in pos_players:
                    for s, e in schedule.get(p["id"], []):
                        if s > 0:
                            all_events.append((s, "on", p["number"]))
                        if e < total:
                            all_events.append((e, "off", p["number"]))
                all_events.sort()

                by_min = {}
                for m, etype, num in all_events:
                    by_min.setdefault(m, {"on": [], "off": []})
                    by_min[m][etype].append(num)

                if by_min:
                    chips_html = '<div style="margin-top:4px;margin-bottom:2px">'
                    for m in sorted(by_min.keys()):
                        q_idx = m // qm
                        rem   = qm - (m % qm) if m % qm != 0 else qm
                        offs  = by_min[m]["off"]
                        ons   = by_min[m]["on"]
                        chips_html += (
                            f'<span style="display:inline-block;padding:3px 8px;border-radius:5px;'
                            f'font-size:11px;font-family:monospace;margin:2px;'
                            f'background:{BG2};border:1px solid {pc}44">'
                            f'<span style="color:{pc};font-weight:700">Q{q_idx+1} {rem:02d}:00</span>'
                        )
                        if offs:
                            chips_html += f' <span style="color:#dc2626">▼ {" ".join("#"+n for n in offs)}</span>'
                        if ons:
                            chips_html += f' <span style="color:#16a34a">▲ {" ".join("#"+n for n in ons)}</span>'
                        chips_html += '</span>'
                    chips_html += '</div>'
                    st.markdown(chips_html, unsafe_allow_html=True)

            st.markdown('</div></div>', unsafe_allow_html=True)  # close min-width + scroll wrapper

            st.divider()

            # ── On-field count bar ───────────────────────────────────────────
            st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:3px;color:{TEXT_MUT};margin-bottom:8px">ON FIELD COUNT</div>', unsafe_allow_html=True)
            bar_html = '<div style="display:flex;height:16px;border-radius:3px;overflow:hidden;font-size:0">'
            for m in range(total):
                c    = on_field_at(schedule, squad_players, m)
                bg   = "#16a34a" if c == tgt else ("#dc2626" if c > tgt else ("#d97706" if c > 0 else (BAR_BG)))
                bord = "border-right:1px solid rgba(0,0,0,0.1);" if (m + 1) % qm == 0 else ""
                bar_html += f'<div style="flex:1;background:{bg};opacity:{0.2 if c==0 else 0.85};{bord}" title="Min {m}: {c}"></div>'
            bar_html += '</div>'
            st.markdown(bar_html, unsafe_allow_html=True)
            st.markdown(
                f'<div style="font-family:monospace;font-size:10px;margin-top:3px;color:{TEXT_MUT}">'
                f'<span style="color:#16a34a">■ correct</span>  '
                f'<span style="color:#d97706">■ under</span>  '
                f'<span style="color:#dc2626">■ over</span></div>',
                unsafe_allow_html=True
            )

            # ── Playing time summary ─────────────────────────────────────────
            st.divider()
            st.markdown(f'<div style="font-family:monospace;font-size:10px;letter-spacing:3px;color:{TEXT_MUT};margin-bottom:8px">PLAYING TIME</div>', unsafe_allow_html=True)
            sorted_players = sorted(squad_players, key=lambda p: -playing_time(schedule, p["id"], total))
            for p in sorted_players:
                pt_val = playing_time(schedule, p["id"], total)
                pct_val = pt_val / total if total else 0
                pk  = p.get("pos", "")
                pc  = POS[pk]["color"] if pk else TEXT_MUT
                pt_col = "#16a34a" if pct_val >= 0.95 else (TEXT if pct_val > 0 else TEXT_MUT)
                c1, c2, c3 = st.columns([2, 7, 1])
                with c1:
                    st.markdown(f'<span style="font-family:monospace;font-size:10px;color:{pc};font-weight:700">{p["name"].split()[0]}</span>', unsafe_allow_html=True)
                with c2:
                    st.progress(pct_val)
                with c3:
                    st.markdown(f'<span style="font-family:monospace;font-size:10px;color:{pt_col};font-weight:700">{pt_val}m</span>', unsafe_allow_html=True)

    # ── AI GENERATE ────────────────────────────────────────────────────────────
    elif st.session_state.match_tab == "ai":
        st.markdown(f"### 🤖 AI Substitution Generator")
        st.markdown(f'<span style="color:{TEXT_MUT}">Describe any special requirements — fair time, player restrictions, tactical priorities.</span>', unsafe_allow_html=True)

        ai_prompt = st.text_area(
            "Constraints",
            placeholder="e.g. Emma needs at least 45 mins, Isla max 20 mins (injury), keep best attack in Q4…",
            height=120,
            label_visibility="collapsed"
        )

        api_key = st.text_input(
            "Anthropic API Key",
            type="password",
            placeholder="sk-ant-...",
            help="Your key is only used for this request and not stored."
        )

        col_gen, col_cancel, _ = st.columns([1.5, 1, 6])
        with col_cancel:
            if st.button("← Back"):
                st.session_state.match_tab = "sub"
                st.rerun()
        with col_gen:
            run_ai = st.button("✨ Generate", type="primary", use_container_width=True)

        if run_ai:
            if not api_key:
                st.error("Please enter your Anthropic API key.")
            elif not squad_players:
                st.error("Add match squad in Setup first.")
            else:
                with st.spinner("Generating substitution schedule…"):
                    import requests
                    squad_info = [
                        {"id": p["id"], "name": p["name"], "number": p["number"],
                         "position": st.session_state.pos_map.get(p["id"], "unknown")}
                        for p in squad_players
                    ]
                    prompt = (
                        "Field hockey sub scheduler. Return ONLY a JSON object mapping "
                        "player id (string) to [[start,end]] segments. "
                        "Formation: " + json.dumps(fmt) + ". "
                        + str(total) + "min match, " + str(qm) + "min quarters, "
                        + str(brk) + "min rest (GKs exempt). "
                        "No subs in first " + str(brk) + " min of a quarter. "
                        "Stagger position group subs so they don't all happen same minute. "
                        "Distribute playing time fairly. Squad: " + json.dumps(squad_info)
                        + (" Constraints: " + ai_prompt if ai_prompt.strip() else "")
                    )
                    try:
                        resp = requests.post(
                            "https://api.anthropic.com/v1/messages",
                            headers={
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json",
                            },
                            json={
                                "model": "claude-sonnet-4-20250514",
                                "max_tokens": 2000,
                                "messages": [{"role": "user", "content": prompt}],
                            },
                            timeout=30,
                        )
                        data = resp.json()
                        raw  = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
                        s_idx, e_idx = raw.index("{"), raw.rindex("}")
                        parsed = json.loads(raw[s_idx:e_idx+1])
                        new_sched = {}
                        for k, v in parsed.items():
                            matched = next((p for p in squad_players if str(p["id"]) == str(k)), None)
                            if matched:
                                new_sched[matched["id"]] = [tuple(seg) for seg in v]
                        st.session_state.schedule.update(new_sched)
                        st.success(f"Generated schedule for {len(new_sched)} players!")
                        st.session_state.match_tab = "sub"
                        st.rerun()
                    except Exception as ex:
                        st.error(f"AI generation failed: {ex}")

# ══════════════════════════════════════════════════════════════════════════════
# GUIDE TAB
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.tab == "guide":

    def section(title, color):
        st.markdown(
            f'<div style="font-family:monospace;font-size:11px;font-weight:700;'
            f'letter-spacing:3px;color:{color};margin-top:24px;margin-bottom:6px">'
            f'{title}</div>',
            unsafe_allow_html=True
        )

    def card(icon, heading, body, color):
        st.markdown(
            f'<div style="background:{CARD_BG};border:1px solid {BORDER};border-left:4px solid {color};'
            f'border-radius:8px;padding:12px 16px;margin-bottom:8px">'
            f'<div style="font-family:monospace;font-size:13px;font-weight:700;color:{color};margin-bottom:4px">'
            f'{icon} {heading}</div>'
            f'<div style="font-family:monospace;font-size:12px;color:{TEXT};line-height:1.7">{body}</div>'
            f'</div>',
            unsafe_allow_html=True
        )

    def step(num, text, color):
        st.markdown(
            f'<div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:8px">'
            f'<div style="min-width:26px;height:26px;border-radius:50%;background:{color};'
            f'display:flex;align-items:center;justify-content:center;'
            f'font-family:monospace;font-size:11px;font-weight:700;color:#fff">{num}</div>'
            f'<div style="font-family:monospace;font-size:12px;color:{TEXT};line-height:1.7;padding-top:3px">{text}</div>'
            f'</div>',
            unsafe_allow_html=True
        )

    # ── Hero banner ─────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="background:linear-gradient(135deg,{CARD_BG},{BG2});'
        f'border:1px solid {BORDER};border-radius:12px;padding:24px 28px;margin-bottom:16px">'
        f'<div style="font-family:monospace;font-size:26px;font-weight:700;color:{TEXT}">🏑 Hockey Manager</div>'
        f'<div style="font-family:monospace;font-size:13px;color:{TEXT_MUT};margin-top:6px;line-height:1.8">'
        f'Plan your substitutions, track playing time, and generate fair rotation schedules for field hockey matches.<br>'
        f'Works best on a tablet or desktop. All data stays in your browser — nothing is sent to a server.</div>'
        f'</div>',
        unsafe_allow_html=True
    )

    left_col, right_col = st.columns([1, 1])

    with left_col:

        # ── Quick start ──────────────────────────────────────────────────────
        section("QUICK START — 5 STEPS", "#16a34a")
        step(1, "<b>Squad tab</b> — Add your players (name + number). Use <b>🧪 Load Test</b> to try it with sample data.", "#16a34a")
        step(2, "<b>Match tab → Setup</b> — Choose quarter length, break time, and formation.", "#2563eb")
        step(3, "<b>Setup → Match Squad</b> — Tick the 18 players attending and assign each a position.", "#7c3aed")
        step(4, "<b>Sub Sheet → ⚡ Auto Generate</b> — Instantly creates a fair rotation schedule.", "#d97706")
        step(5, "<b>Adjust or print</b> — Tweak individual times with the ▼ editor, then hit 🖨️ Print Sheet.", "#dc2626")

        # ── Squad tab ────────────────────────────────────────────────────────
        section("👥 SQUAD TAB", "#2563eb")
        card("➕", "Adding players",
             "Type a shirt number and full name, then click <b>➕ Add</b>.<br>"
             "Players are saved to your roster for all future matches.",
             "#2563eb")
        card("✕", "Removing players",
             "Click the <b>✕</b> button next to any player to remove them from the roster.",
             "#dc2626")
        card("🧪", "Test data",
             "Click <b>🧪 Load Test</b> to populate 18 sample players so you can explore the app without entering real names.",
             "#059669")

        # ── Save / Load ──────────────────────────────────────────────────────
        section("💾 SAVE / LOAD TEAM", "#d97706")
        card("💾", "Saving on the same browser",
             "Open the <b>💾 Save / Load Team</b> panel, type a team name (e.g. <i>U16 Greens</i>), and click Save.<br>"
             "The roster is stored in your browser's local storage and will survive page refreshes.",
             "#d97706")
        card("📋", "Sharing to another device",
             "Copy the JSON shown in the <b>📋 Copy this to save/share</b> box.<br>"
             "On the other device, paste it into the Load panel and click <b>📂 Load Team</b>.",
             "#7c3aed")

    with right_col:

        # ── Match setup ──────────────────────────────────────────────────────
        section("⚙️ MATCH SETUP", "#7c3aed")
        card("⏱️", "Quarter length",
             "Choose from 10 / 12 / 15 / 20 min presets or type a custom value.<br>"
             "The app calculates total match time as 4 × quarter length.",
             "#7c3aed")
        card("😴", "Min break time",
             "The minimum minutes a player must rest before coming back on.<br>"
             "GKs are exempt and play full halves (or the whole match if only one).",
             "#059669")
        card("🔢", "Formation",
             "Pick a preset (4-3-3, 4-4-2 etc.) or choose <b>Custom</b> to set exact player counts per position.<br>"
             "The on-field count bar will turn red if the schedule ever exceeds this number.",
             "#2563eb")
        card("✅", "Match squad",
             "Tick up to 18 players, then use the dropdown to assign each a position.<br>"
             "Players without a position will trigger a warning — they won't be included in auto-generate.",
             "#dc2626")

        # ── Sub sheet ────────────────────────────────────────────────────────
        section("📋 SUB SHEET", "#dc2626")
        card("⚡", "Auto Generate",
             "Creates a staggered rotation so each position group subs at different times.<br>"
             "Playing time is spread fairly across the squad. GKs are handled separately.",
             "#16a34a")
        card("▼", "Editing a player's times",
             "Click the <b>▼ Xm Y%</b> button next to a player's bar to expand their segment editor.<br>"
             "Use the <b>On / Off</b> number fields to adjust when they enter and leave.<br>"
             "Click <b>＋ Add segment</b> to add a second stint, or <b>✕</b> to delete one.",
             "#d97706")
        card("✏️", "Edit Times panel",
             "Click <b>✏️ Edit Times</b> for a text-based editor — type segments as <code>0-30, 35-60</code>.<br>"
             "Useful for quickly setting multiple players at once.",
             "#7c3aed")
        card("🖨️", "Print Sheet",
             "Generates a landscape A4 page with the Gantt chart and substitution events table.<br>"
             "The print dialog opens automatically in a new window.",
             "#2563eb")
        card("🤖", "AI Generate",
             "Enter your Anthropic API key and any special constraints (e.g. <i>Emma needs 45+ mins</i>).<br>"
             "The AI will build a custom schedule respecting your requirements.",
             "#059669")

        # ── Reading the Gantt ────────────────────────────────────────────────
        section("📊 READING THE GANTT", "#059669")
        card("🟩", "On-field count bar",
             "<span style='color:#16a34a'>■ Green</span> = correct number on field · "
             "<span style='color:#d97706'>■ Amber</span> = too few · "
             "<span style='color:#dc2626'>■ Red</span> = too many.<br>"
             "Aim for a fully green bar across all 60 minutes.",
             "#059669")
        card("📏", "Bar labels",
             "Wide segments show the start–end time inside the bar.<br>"
             "Narrow segments show a small label <i>above</i> the bar to keep things readable.<br>"
             "Sub-from labels appear <i>below</i> the bar (e.g. ↑JSM Q2:05 = subbed for JSM with 5min left in Q2).",
             "#2563eb")

    # ── Positions key ────────────────────────────────────────────────────────
    section("🎨 POSITION COLOUR KEY", TEXT_MUT)
    pos_cols = st.columns(5)
    for i, (pk, pv) in enumerate(POS.items()):
        with pos_cols[i]:
            st.markdown(
                f'<div style="text-align:center;background:{pv["color"]}18;'
                f'border:1px solid {pv["color"]}55;border-radius:8px;padding:10px 4px">'
                f'<div style="font-size:20px;font-weight:700;color:{pv["color"]};font-family:monospace">{pk}</div>'
                f'<div style="font-size:10px;color:{pv["color"]};font-family:monospace">{pv["label"]}</div>'
                f'</div>',
                unsafe_allow_html=True
            )

    st.markdown(
        f'<div style="font-family:monospace;font-size:10px;color:{TEXT_MUT};'
        f'text-align:center;margin-top:24px">Hockey Manager · built with Streamlit · all data stored locally in your browser</div>',
        unsafe_allow_html=True
    )