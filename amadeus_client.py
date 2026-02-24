import requests
import streamlit as st


def get_amadeus_token():
    base_url = st.secrets.get("AMADEUS_BASE_URL", "https://test.api.amadeus.com")
    client_id = st.secrets["AMADEUS_CLIENT_ID"]
    client_secret = st.secrets["AMADEUS_CLIENT_SECRET"]

    url = f"{base_url}/v1/security/oauth2/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    resp = requests.post(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload["access_token"]


def search_roundtrip_flights(
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    return_date: str,
    adults: int = 1,
    currency: str = "EUR",
    max_results: int = 5,
):
    """
    Nutzt Amadeus Flight Offers Search (v2).
    Gibt rohe JSON-Antwort zurück.
    """
    base_url = st.secrets.get("AMADEUS_BASE_URL", "https://test.api.amadeus.com")
    token = get_amadeus_token()

    url = f"{base_url}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": origin_iata,
        "destinationLocationCode": destination_iata,
        "departureDate": departure_date,   # YYYY-MM-DD
        "returnDate": return_date,         # YYYY-MM-DD
        "adults": adults,
        "currencyCode": currency,
        "max": max_results,
    }

    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def extract_cheapest_offer_summary(flight_offers_json):
    """
    Gibt die günstigste Option in einfacher Form zurück:
    { price_total, stops_outbound, stops_inbound }
    oder None, wenn keine Daten.
    """
    data = flight_offers_json.get("data", [])
    if not data:
        return None

    cheapest = None

    for offer in data:
        try:
            price_total = float(offer["price"]["grandTotal"])
            itineraries = offer.get("itineraries", [])

            # stops = Segmente - 1
            stops_outbound = max(len(itineraries[0].get("segments", [])) - 1, 0) if len(itineraries) > 0 else 99
            stops_inbound = max(len(itineraries[1].get("segments", [])) - 1, 0) if len(itineraries) > 1 else 99

            row = {
                "price_total": price_total,
                "stops_outbound": stops_outbound,
                "stops_inbound": stops_inbound,
            }

            if cheapest is None or row["price_total"] < cheapest["price_total"]:
                cheapest = row
        except Exception:
            continue

    return cheapest