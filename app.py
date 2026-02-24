import streamlit as st
import pandas as pd
from datetime import date, timedelta
from amadeus_client import search_roundtrip_flights, extract_cheapest_offer_summary

st.set_page_config(page_title="MeetMeCheap MVP", layout="wide")

st.title("MeetMeCheap — gemeinsamer günstiger Tripfinder (MVP)")
st.caption("Version 2: Echte Flugdaten (Amadeus Test API) + Ranking für 1 oder 2 Startstädte")

# -----------------------------
# Config (klein halten = gratis-freundlich)
# -----------------------------
DESTINATIONS = [
    {"destination": "Rom", "country": "Italien", "iata": "FCO"},
    {"destination": "Mailand", "country": "Italien", "iata": "MXP"},
    {"destination": "Neapel", "country": "Italien", "iata": "NAP"},
    {"destination": "Barcelona", "country": "Spanien", "iata": "BCN"},
    {"destination": "Valencia", "country": "Spanien", "iata": "VLC"},
    {"destination": "Palma", "country": "Spanien", "iata": "PMI"},
    {"destination": "Lissabon", "country": "Portugal", "iata": "LIS"},
    {"destination": "Porto", "country": "Portugal", "iata": "OPO"},
    {"destination": "Athen", "country": "Griechenland", "iata": "ATH"},
    {"destination": "Prag", "country": "Tschechien", "iata": "PRG"},
    {"destination": "Budapest", "country": "Ungarn", "iata": "BUD"},
    {"destination": "Kopenhagen", "country": "Dänemark", "iata": "CPH"},
]

SUPPORTED_COUNTRIES = sorted(list({d["country"] for d in DESTINATIONS}))

CITY_TO_IATA = {
    "BERLIN": "BER",
    "BER": "BER",
    "WIEN": "VIE",
    "VIENNA": "VIE",
    "VIE": "VIE",
    "MÜNCHEN": "MUC",
    "MUENCHEN": "MUC",
    "MUNICH": "MUC",
    "MUC": "MUC",
    "HAMBURG": "HAM",
    "HAM": "HAM",
    "FRANKFURT": "FRA",
    "FRA": "FRA",
}

# -----------------------------
# Sidebar Inputs
# -----------------------------
st.sidebar.header("Eingaben")

origin_a = st.sidebar.text_input("Startstadt A (IATA oder Name)", value="Berlin")
origin_b = st.sidebar.text_input(
    "Startstadt B (optional, IATA oder Name)",
    value="Wien",
    help="Leer lassen = Single-Origin-Modus (nur günstigste Flüge von Start A)"
)

date_range = st.sidebar.date_input(
    "Suchzeitraum (Abflugdatum von–bis)",
    value=(date(2026, 4, 1), date(2026, 6, 30)),
    min_value=date(2026, 1, 1),
    max_value=date(2027, 12, 31),
    help="Es werden Trips angezeigt, deren Abflugdatum in diesem Zeitraum liegt."
)

country_filter = st.sidebar.multiselect(
    "Zielländer (optional)",
    options=SUPPORTED_COUNTRIES,
    default=[]
)

nights_range = st.sidebar.slider("Reisedauer (Nächte)", min_value=2, max_value=10, value=(3, 5))
budget_per_person = st.sidebar.number_input("Budget pro Person (optional, €)", min_value=0, value=0, step=10)
nonstop_only = st.sidebar.checkbox("Nur Direktflüge", value=False)

st.sidebar.markdown("---")
max_destinations = st.sidebar.slider("Max. Ziele prüfen (API-Calls sparen)", 3, 12, 6)
max_date_windows = st.sidebar.slider("Max. Datumsfenster prüfen (API-Calls sparen)", 1, 10, 4)

# Button-Text je nach Modus
single_mode_preview = (origin_b.strip() == "")
find_btn_label = "Günstige Flüge finden" if single_mode_preview else "Günstige gemeinsame Trips finden"
find_btn = st.sidebar.button(find_btn_label)

# -----------------------------
# Helpers
# -----------------------------
def normalize_origin_to_iata(value: str) -> str:
    if not value:
        return ""
    cleaned = value.strip().upper()
    return CITY_TO_IATA.get(cleaned, cleaned)

def parse_date_range(input_value):
    if isinstance(input_value, (tuple, list)) and len(input_value) == 2:
        start_date, end_date = input_value[0], input_value[1]
        if start_date and end_date and start_date > end_date:
            start_date, end_date = end_date, start_date
        return start_date, end_date
    if input_value:
        return input_value, input_value
    return None, None

def generate_trip_windows(start_date: date, end_date: date, min_nights: int, max_nights: int, max_windows: int = 4):
    windows = []
    if not start_date or not end_date:
        return windows

    current = start_date
    preferred_nights = sorted(set([min_nights, max_nights, (min_nights + max_nights) // 2]))

    while current <= end_date and len(windows) < max_windows:
        added = False
        for nights in preferred_nights:
            ret = current + timedelta(days=int(nights))
            if ret <= (end_date + timedelta(days=max_nights)):
                windows.append({
                    "depart_date": current.isoformat(),
                    "return_date": ret.isoformat(),
                    "nights": nights,
                })
                added = True
                if len(windows) >= max_windows:
                    break
        current += timedelta(days=7)
        if not added and current > end_date:
            break

    unique = []
    seen = set()
    for w in windows:
        key = (w["depart_date"], w["return_date"], w["nights"])
        if key not in seen:
            seen.add(key)
            unique.append(w)
    return unique[:max_windows]

def score_joint_results(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["total_price"] = df["price_a"] + df["price_b"]
    df["fairness_gap"] = (df["price_a"] - df["price_b"]).abs()
    df["travel_penalty"] = (df["stops_a"] + df["stops_b"]) * 15
    df["score"] = df["total_price"] + 0.35 * df["fairness_gap"] + df["travel_penalty"]
    return df.sort_values(["score", "total_price", "fairness_gap"], ascending=True)

def score_single_results(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["travel_penalty"] = df["stops_a"] * 15
    df["score"] = df["price_a"] + df["travel_penalty"]
    return df.sort_values(["score", "price_a"], ascending=True)

def apply_post_filters(df: pd.DataFrame, single_mode: bool) -> pd.DataFrame:
    df = df.copy()

    if country_filter:
        df = df[df["country"].isin(country_filter)]

    min_nights, max_nights = nights_range
    df = df[(df["nights"] >= min_nights) & (df["nights"] <= max_nights)]

    if budget_per_person and budget_per_person > 0:
        if single_mode:
            df = df[df["price_a"] <= budget_per_person]
        else:
            df = df[(df["price_a"] <= budget_per_person) & (df["price_b"] <= budget_per_person)]

    if nonstop_only:
        if single_mode:
            df = df[df["stops_a"] == 0]
        else:
            df = df[(df["stops_a"] == 0) & (df["stops_b"] == 0)]

    return df

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_cheapest_for_leg(origin_iata: str, destination_iata: str, departure_date: str, return_date: str):
    raw = search_roundtrip_flights(
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        departure_date=departure_date,
        return_date=return_date,
        adults=1,
        currency="EUR",
        max_results=5,
    )
    return extract_cheapest_offer_summary(raw)

def build_real_results(origin_a_iata: str, origin_b_iata: str, start_date: date, end_date: date, single_mode: bool) -> pd.DataFrame:
    min_nights, max_nights = nights_range
    windows = generate_trip_windows(
        start_date=start_date,
        end_date=end_date,
        min_nights=min_nights,
        max_nights=max_nights,
        max_windows=max_date_windows,
    )

    if not windows:
        return pd.DataFrame()

    candidates = DESTINATIONS
    if country_filter:
        candidates = [d for d in candidates if d["country"] in country_filter]
    candidates = candidates[:max_destinations]

    rows = []
    calls_per_combo = 1 if single_mode else 2
    total_calls_est = len(candidates) * len(windows) * calls_per_combo
    progress = st.progress(0)
    status = st.empty()
    done_calls = 0

    for dest in candidates:
        for w in windows:
            if single_mode:
                status.info(
                    f"Suche: {origin_a_iata} → {dest['destination']} "
                    f"({w['depart_date']} bis {w['return_date']})"
                )
            else:
                status.info(
                    f"Suche: {origin_a_iata}/{origin_b_iata} → {dest['destination']} "
                    f"({w['depart_date']} bis {w['return_date']})"
                )

            try:
                quote_a = fetch_cheapest_for_leg(
                    origin_iata=origin_a_iata,
                    destination_iata=dest["iata"],
                    departure_date=w["depart_date"],
                    return_date=w["return_date"],
                )
            except Exception:
                quote_a = None
            done_calls += 1
            progress.progress(min(done_calls / max(total_calls_est, 1), 1.0))

            if not quote_a:
                continue

            if single_mode:
                rows.append({
                    "destination": dest["destination"],
                    "country": dest["country"],
                    "destination_iata": dest["iata"],
                    "depart_date": w["depart_date"],
                    "return_date": w["return_date"],
                    "nights": w["nights"],
                    "price_a": float(quote_a["price_total"]),
                    "stops_a": int(quote_a["stops_outbound"]) + int(quote_a["stops_inbound"]),
                })
                continue

            try:
                quote_b = fetch_cheapest_for_leg(
                    origin_iata=origin_b_iata,
                    destination_iata=dest["iata"],
                    departure_date=w["depart_date"],
                    return_date=w["return_date"],
                )
            except Exception:
                quote_b = None
            done_calls += 1
            progress.progress(min(done_calls / max(total_calls_est, 1), 1.0))

            if not quote_b:
                continue

            rows.append({
                "destination": dest["destination"],
                "country": dest["country"],
                "destination_iata": dest["iata"],
                "depart_date": w["depart_date"],
                "return_date": w["return_date"],
                "nights": w["nights"],
                "price_a": float(quote_a["price_total"]),
                "price_b": float(quote_b["price_total"]),
                "stops_a": int(quote_a["stops_outbound"]) + int(quote_a["stops_inbound"]),
                "stops_b": int(quote_b["stops_outbound"]) + int(quote_b["stops_inbound"]),
            })

    progress.empty()
    status.empty()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["depart_date_dt"] = pd.to_datetime(df["depart_date"]).dt.date
    df["return_date_dt"] = pd.to_datetime(df["return_date"]).dt.date
    return df

# -----------------------------
# Main
# -----------------------------
origin_a_iata = normalize_origin_to_iata(origin_a)
origin_b_iata = normalize_origin_to_iata(origin_b) if origin_b.strip() else ""
single_mode = (origin_b_iata == "")

start_date, end_date = parse_date_range(date_range)

if single_mode:
    st.write(f"**Modus:** Single-Origin")
    st.write(f"**Start A:** {origin_a} → `{origin_a_iata}`")
else:
    st.write(f"**Modus:** Two-Origin")
    st.write(f"**Start A:** {origin_a} → `{origin_a_iata}`  |  **Start B:** {origin_b} → `{origin_b_iata}`")

if start_date and end_date:
    st.write(f"**Suchzeitraum:** {start_date} bis {end_date}")

with st.expander("Hinweise (wichtig für gratis Nutzung)", expanded=False):
    st.markdown(
        "- Diese Version nutzt die **Amadeus Test API** (echte API, aber Test/Quota-begrenzt).\n"
        "- Um im Gratis-Bereich zu bleiben, sind **Ziele** und **Datumsfenster** begrenzt.\n"
        "- Nutze am besten IATA-Codes wie `BER`, `VIE` (Namen wie Berlin/Wien werden teilweise gemappt).\n"
        "- Lässt du Start B leer, läuft ein **Single-Origin-Testmodus** (gut zum Preisvergleich mit Skyscanner).\n"
        "- Erste Ergebnisse können langsam sein; wiederholte gleiche Suchen sind durch Cache schneller."
    )

# Optionaler API-Testbutton
if st.sidebar.button("API-Test BER → FCO (15.–18.05.2026)"):
    try:
        raw_test = search_roundtrip_flights(
            origin_iata="BER",
            destination_iata="FCO",
            departure_date="2026-05-15",
            return_date="2026-05-18",
            adults=1,
            currency="EUR",
            max_results=5,
        )
        cheapest_test = extract_cheapest_offer_summary(raw_test)
        st.success("API-Test erfolgreich ✅")
        st.json(cheapest_test if cheapest_test else {"message": "Keine Angebote gefunden"})
    except Exception as e:
        st.error(f"API-Test fehlgeschlagen: {e}")

if not find_btn:
    st.info("Links Eingaben setzen und auf den Such-Button klicken.")
else:
    if not origin_a_iata or len(origin_a_iata) != 3:
        st.error("Startstadt A bitte als gültigen IATA-Code (z. B. BER) oder unterstützten Städtenamen eingeben.")
        st.stop()

    if (not single_mode) and (not origin_b_iata or len(origin_b_iata) != 3):
        st.error("Startstadt B bitte als gültigen IATA-Code (z. B. VIE) oder leer lassen.")
        st.stop()

    if not start_date or not end_date:
        st.error("Bitte einen gültigen Suchzeitraum auswählen.")
        st.stop()

    try:
        df = build_real_results(origin_a_iata, origin_b_iata, start_date, end_date, single_mode=single_mode)
    except Exception as e:
        st.error(f"Fehler bei der Flugsuche: {e}")
        st.stop()

    if df.empty:
        if single_mode:
            st.warning("Keine Ergebnisse gefunden. Versuche mehr Länder, größeren Zeitraum oder mehr Datumsfenster.")
        else:
            st.warning("Keine gemeinsamen Ergebnisse gefunden. Versuche mehr Länder, größeren Zeitraum oder mehr Datumsfenster.")
        st.stop()

    df = apply_post_filters(df, single_mode=single_mode)
    if df.empty:
        st.warning("Ergebnisse gefunden, aber nach Filtern (Budget/Nonstop/Nächte) bleibt nichts übrig.")
        st.stop()

    if single_mode:
        ranked = score_single_results(df)

        out = ranked.copy()
        out["Ziel"] = out["destination"]
        out["Land"] = out["country"]
        out["Abflug"] = out["depart_date"]
        out["Rückflug"] = out["return_date"]
        out["Nächte"] = out["nights"]
        out["Preis (€)"] = out["price_a"].round(0).astype(int)
        out["Stops (hin+zurück)"] = out["stops_a"].astype(int)

        display_cols = [
            "Ziel", "Land", "Abflug", "Rückflug", "Nächte",
            "Preis (€)", "Stops (hin+zurück)"
        ]

        st.subheader("Günstigste Flüge (Single-Origin, echte API-Daten)")
        st.dataframe(out[display_cols].reset_index(drop=True), use_container_width=True)

        st.subheader("Top 3 (kompakt)")
        top3 = out[display_cols].head(3).to_dict(orient="records")
        for i, row in enumerate(top3, start=1):
            st.markdown(
                f"**{i}. {row['Ziel']} ({row['Land']})** — {row['Nächte']} Nächte "
                f"({row['Abflug']} bis {row['Rückflug']}) • "
                f"**Preis: {row['Preis (€)']}€** "
                f"(Stops: {row['Stops (hin+zurück)']})"
            )

        csv = out[display_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "Ergebnisse als CSV herunterladen",
            data=csv,
            file_name="meetmecheap_results_single_origin.csv",
            mime="text/csv"
        )

    else:
        ranked = score_joint_results(df)

        out = ranked.copy()
        out["Ziel"] = out["destination"]
        out["Land"] = out["country"]
        out["Abflug"] = out["depart_date"]
        out["Rückflug"] = out["return_date"]
        out["Nächte"] = out["nights"]
        out["Preis A (€)"] = out["price_a"].round(0).astype(int)
        out["Preis B (€)"] = out["price_b"].round(0).astype(int)
        out["Gesamtpreis (€)"] = out["total_price"].round(0).astype(int)
        out["Fairness-Diff (€)"] = out["fairness_gap"].round(0).astype(int)
        out["Stops A (hin+zurück)"] = out["stops_a"].astype(int)
        out["Stops B (hin+zurück)"] = out["stops_b"].astype(int)

        display_cols = [
            "Ziel", "Land", "Abflug", "Rückflug", "Nächte",
            "Preis A (€)", "Preis B (€)", "Gesamtpreis (€)", "Fairness-Diff (€)",
            "Stops A (hin+zurück)", "Stops B (hin+zurück)"
        ]

        st.subheader("Top gemeinsame Reiseideen (Two-Origin, echte API-Daten)")
        st.dataframe(out[display_cols].reset_index(drop=True), use_container_width=True)

        st.subheader("Top 3 (kompakt)")
        top3 = out[display_cols].head(3).to_dict(orient="records")
        for i, row in enumerate(top3, start=1):
            st.markdown(
                f"**{i}. {row['Ziel']} ({row['Land']})** — {row['Nächte']} Nächte "
                f"({row['Abflug']} bis {row['Rückflug']}) • "
                f"A: {row['Preis A (€)']}€ • B: {row['Preis B (€)']}€ • "
                f"**Gesamt: {row['Gesamtpreis (€)']}€** "
                f"(Stops A: {row['Stops A (hin+zurück)']}, Stops B: {row['Stops B (hin+zurück)']})"
            )

        csv = out[display_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "Ergebnisse als CSV herunterladen",
            data=csv,
            file_name="meetmecheap_results_two_origin.csv",
            mime="text/csv"
        )

st.markdown("---")
st.caption("Nächster Schritt: Zielliste erweitern, echte Stadt→IATA-Suche, bessere Datumsfenster-Strategie, Links zur Buchung.")