import requests
from requests.auth import HTTPBasicAuth

# TheTVDB Authentication Information
the_tvdb_authentication = {
    "apikey": "",
    "userkey": "",  # Unique User Key
    "username": ""
}

# Plex Server Information
plex_server = "http://localhost:32400"
plex_username = ""
plex_password = ""

# Array of show names to ignore, examples included
ignore_list = [
    # "The Big Bang Theory",
    # "Dirty Jobs",
    # "Street Outlaws"
]

# Ignore Plex Certificate Issues
if "https" in plex_server:
    requests.packages.urllib3.disable_warnings()
    requests.adapters.DEFAULT_RETRIES = 3
    session = requests.Session()
    session.verify = False


# Try to authenticate with TheTVDB API to get a token
try:
    response = requests.post(
        "https://api.thetvdb.com/login",
        json=the_tvdb_authentication,
        headers={"Content-Type": "application/json"}
    )
    the_tvdb_token = response.json().get("token")
except requests.exceptions.RequestException as e:
    print("Failed to get TheTVDB API Token:")
    print(e)
    raise SystemExit


# Create TheTVDB API Headers
tvdb_headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {the_tvdb_token}"
}

# Create Plex Headers
plex_headers = {
    "Authorization": "Basic " + (plex_username + ":" + plex_password).encode("utf-8").b64encode().decode("utf-8"),
    "X-Plex-Client-Identifier": "MissingTVEpisodes",
    "X-Plex-Product": "PowerShell",
    "X-Plex-Version": "V1"
}

# Try to get Plex Token
try:
    response = requests.post(
        "https://plex.tv/users/sign_in.json",
        headers=plex_headers
    )
    plex_token = response.json().get("user").get("authToken")
    plex_headers["X-Plex-Token"] = plex_token
    del plex_headers["Authorization"]
except requests.exceptions.RequestException as e:
    print("Failed to get Plex Auth Token:")
    print(e)
    raise SystemExit


# Try to get the Library IDs for TV Shows
try:
    response = requests.get(
        f"{plex_server}/library/sections",
        headers=plex_headers
    )
    tv_keys = [
        section["key"] for section in response.json().get("MediaContainer", {}).get("Directory", [])
        if section.get("type") == "show"
    ]
except requests.exceptions.RequestException as e:
    print("Failed to get Plex Library Sections:")
    if e.response.status_code == 401:
        print("Ensure that your source IP is configured under the 'List of IP addresses and networks that are allowed without auth' setting")
    else:
        print(e)
    raise SystemExit


# Get all RatingKeys
rating_keys = []
for tv_key in tv_keys:
    response = requests.get(
        f"{plex_server}/library/sections/{tv_key}/all/",
        headers=plex_headers
    )
    series_info = response.json().get("MediaContainer", {}).get("Directory", [])
    for series in series_info:
        if series["title"] not in ignore_list:
            rating_keys.append(series["ratingKey"])
rating_keys = sorted(set(rating_keys))


# Get all Show Data
plex_shows = {}
progress = 0
for rating_key in rating_keys:
    response = requests.get(
        f"{plex_server}/library/metadata/{rating_key}/",
        headers=plex_headers
    )
    show_data = response.json().get("MediaContainer", {}).get("Directory", [])
    progress += 1
    print(f"Collecting Show Data: {show_data['title']} - {progress / len(rating_keys) * 100:.2f}% Complete")
    guid = show_data["guid"].replace(".*//(\d+).*", "\\1")
    if guid in plex_shows:
        plex_shows[guid]["ratingKeys"].append(rating_key)
    else:
        plex_shows[guid] = {
            "title": show_data["title"],
            "ratingKeys": [rating_key],
            "seasons": {}
        }


# Get Season data from Show Data
progress = 0
for guid in plex_shows.keys():
    progress += 1
    print(f"Collecting Season Data: {plex_shows[guid]['title']} - {progress / len(plex_shows) * 100:.2f}% Complete")
    for rating_key in plex_shows[guid]["ratingKeys"]:
        response = requests.get(
            f"{plex_server}/library/metadata/{rating_key}/allLeaves",
            headers=plex_headers
        )
        episodes = response.json().get("MediaContainer", {}).get("Video", [])
        seasons = sorted(set(episode["parentIndex"] for episode in episodes))
        for season in seasons:
            if season not in plex_shows[guid]["seasons"]:
                plex_shows[guid]["seasons"][season] = []
        for episode in episodes:
            if not episode.get("parentIndex") or not episode.get("index"):
                print("Missing parentIndex or index")
                print(plex_shows[guid])
                print(episode)
            else:
                plex_shows[guid]["seasons"][episode["parentIndex"]].append(
                    {episode["index"]: episode["title"]}
                )


# Missing Episodes
missing = {}
progress = 0
for guid in plex_shows.keys():
    progress += 1
    print(f"Collecting Episode Data from TheTVDB: {plex_shows[guid]['title']} - {progress / len(plex_shows) * 100:.2f}% Complete")
    page = 1
    episodes = []
    while True:
        try:
            response = requests.get(
                f"https://api.thetvdb.com/series/{guid}/episodes?page={page}",
                headers=tvdb_headers
            )
            result = response.json()
            episodes += result.get("data", [])
            if page >= result["links"]["last"]:
                break
            page += 1
        except requests.exceptions.RequestException:
            print(f"Failed to get Episodes for {plex_shows[guid]['title']}")
            episodes = []
            break
    for episode in episodes:
        if not episode.get("airedSeason"):
            continue  # Ignore episodes with blank airedSeasons (#11)
        if episode["airedSeason"] == 0:
            continue  # Ignore Season 0 / Specials
        if not episode.get("firstAired"):
            continue  # Ignore unaired episodes
        if (datetime.now() - datetime.strptime(episode["firstAired"], "%Y-%m-%d")).days < 1:
            continue  # Ignore episodes that aired in the last ~24 hours
        season_key = str(episode["airedSeason"])
        if episode["episodeName"] not in plex_shows[guid]["seasons"][season_key].values():
            if episode["airedEpisodeNumber"] not in plex_shows[guid]["seasons"][season_key].keys():
                if plex_shows[guid]["title"] not in missing:
                    missing[plex_shows[guid]["title"]] = []
                missing[plex_shows[guid]["title"]].append(
                    {
                        "airedSeason": episode["airedSeason"],
                        "airedEpisodeNumber": episode["airedEpisodeNumber"],
                        "episodeName": episode["episodeName"]
                    }
                )


# Print missing episodes
for show in sorted(missing.keys()):
    for season in sorted(set(episode["airedSeason"] for episode in missing[show])):
        episodes = [episode for episode in missing[show] if episode["airedSeason"] == season]
        for episode in episodes:
            print(f"{show} S{int(season):02d}E{int(episode['airedEpisodeNumber']):02d} - {episode['episodeName']}")
