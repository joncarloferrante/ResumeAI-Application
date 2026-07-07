import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE_URL = "https://atlanticrecruiters.com"
START_URL = "https://atlanticrecruiters.com/jobs/"

def get_soup(url):
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")

def main():
    soup = get_soup(START_URL)

    division_links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "/jobs/division/" in href:
            full_url = urljoin(BASE_URL, href)
            name = a.get_text(strip=True)

            if full_url not in [item["url"] for item in division_links]:
                division_links.append({
                    "name": name,
                    "url": full_url
                })

    for division in division_links:
        print("----------------------------------------")
        print(division["name"])
        print(division["url"])

if __name__ == "__main__":
    main()