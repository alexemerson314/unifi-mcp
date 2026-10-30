"""ClientWifiDetails must keep every field the manager reads from /stat/sta."""

from unifi_core.network.models.sessions import client_wifi_details_from_controller

RAW = {
    "mac": "aa:bb:cc:dd:ee:ff",
    "essid": "home",
    "ap_mac": "11:22:33:44:55:66",
    "signal": -61,
    "noise": -95,
    "satisfaction": 98,
    "tx_rate": 433000,
    "rx_rate": 390000,
    "tx_retries": 12,
    "wifi_tx_attempts": 900,
    "roam_count": 2,
    "channel": 60,
    "radio": "na",
    "bssid": "11:22:33:44:55:77",
    "nss": 2,
    "is_11r": False,
}


def test_all_manager_fields_survive_model_dump():
    dumped = client_wifi_details_from_controller(RAW).model_dump(exclude_none=True)
    assert dumped == {
        "mac": "aa:bb:cc:dd:ee:ff",
        "ssid": "home",
        "ap": "11:22:33:44:55:66",
        "signal": -61,
        "noise": -95,
        "satisfaction": 98,
        "tx_rate": 433000,
        "rx_rate": 390000,
        "tx_retries": 12,
        "wifi_tx_attempts": 900,
        "roam_count": 2,
        "channel": 60,
        "radio": "na",
        "bssid": "11:22:33:44:55:77",
        "nss": 2,
        "is_11r": False,
    }
