import requests
from datetime import datetime
import sqlite3
from bs4 import BeautifulSoup
import json
from difflib import SequenceMatcher
import PyPDF2
import re
from openai import OpenAI
import os
from io import BytesIO
import time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import getpass
import sys


load_dotenv()  # Load variables from .env file
OPENAI_API_KEY = os.getenv("MY_API_KEY")
LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")
today_date = datetime.now().strftime("%Y-%m-%d")

# TODO automatically months calculating, having in mind leap year OR add 2-7 sem
sem1 = {
    "october": "https://moodle2.e-wsb.pl/calendar/view.php?view=month&time=1727733600", # last part is the number of seconds counting from 1970 taking last day as measurement's point
    "november": "https://moodle2.e-wsb.pl/calendar/view.php?view=month&time=1730415600",
    "december": "https://moodle2.e-wsb.pl/calendar/view.php?view=month&time=1733007600",
    "january": "https://moodle2.e-wsb.pl/calendar/view.php?view=month&time=1735686000",
    "february": "https://moodle2.e-wsb.pl/calendar/view.php?view=month&time=1738364400"
}





def capture_cookies_and_userid(username, password):
    cookie_data = {}  # Dictionary to store the extracted sesskey and cookies

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Define a callback function to handle response events
        def capture_response(response):
            nonlocal cookie_data  # Use the external variable cookie_data
            # Check if the response contains 'sesskey' in its URL
            if 'sesskey' in response.url and 'sesskey' not in cookie_data:
                # Extract sesskey using regex from the URL
                sesskey_match = re.search(r'sesskey=([A-Za-z0-9]+)', response.url)
                if sesskey_match:
                    cookie_data['sesskey'] = sesskey_match.group(1)

        # Attach the response event listener to the page
        page.on('response', capture_response)

        # Define a callback function to handle request finished events
        def capture_request_finished(request):
            response = request.response()
            if response:
                try:
                    # Decode the response body and search for userid
                    body = response.body().decode('utf-8')
                    userid_match = re.search(r'userid["\'\s:=]+([0-9]+)', body)
                    if userid_match and 'userid' not in cookie_data:
                        cookie_data['userid'] = userid_match.group(1)
                except Exception:
                    pass  # Ignore errors in decoding response body

        # Attach the request finished event listener to the page
        page.on('requestfinished', capture_request_finished)

        # Navigate to the login page
        page.goto("https://login.wsb.pl/cas/login?service=https%3A%2F%2Fmoodle2.e-wsb.pl%2Flogin%2Findex.php%3FauthCAS%3DCAS")

        # Wait for the page to load
        page.wait_for_load_state("networkidle")

        # Log in
        page.fill("input#username", username)
        page.fill("input#password", password)
        page.click("button#submitButton")

        # Wait for the login process to complete
        page.wait_for_load_state("networkidle")

        # Extract cookies after login
        cookies = page.context.cookies()

        # Add 'MoodleSession' and 'MOODLEID1_' cookies to cookie_data
        for cookie in cookies:
            if cookie['name'] == 'MoodleSession' and 'MoodleSession' not in cookie_data:
                cookie_data['MoodleSession'] = cookie['value']
            elif cookie['name'] == 'MOODLEID1_' and 'MOODLEID1_' not in cookie_data:
                cookie_data['MOODLEID1_'] = cookie['value']

        # Method 1: Check the HTML content for userid
        html_content = page.content()
        userid_match = re.search(r'userid["\'\s:=]+([0-9]+)', html_content)
        if userid_match and 'userid' not in cookie_data:
            cookie_data['userid'] = userid_match.group(1)

        # Method 2: Check JavaScript variables for userid
        userid_js = page.evaluate("() => window.userid || null")
        if userid_js and 'userid' not in cookie_data:
            cookie_data['userid'] = userid_js

        # Close the browser
        browser.close()

    return cookie_data  # Return the dictionary containing sesskey, cookies, and userid



# Modified to be able to take cookie_data
def extract_events_by_month(month, cookie_data):
    session = requests.Session()
    session.cookies.update({
        "MoodleSession": cookie_data['MoodleSession'],
        "MOODLEID1_": cookie_data['MOODLEID1_']
    })


    res = session.get(month)
    print(res.status_code)

    soup = BeautifulSoup(res.text, "html.parser")

    # Connect to the SQLite database (create if it doesn't exist)
    db_filename = "events.db"
    conn = sqlite3.connect(db_filename)
    cursor = conn.cursor()

    # Create the events table if it doesn't exist
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT UNIQUE,
        title TEXT,
        event_name TEXT,
        link TEXT
    )
    """)

    # Find all <td> elements with class 'hasevent'
    td_elements = soup.find_all("td", class_="hasevent")

    if not td_elements:
        print("No <td> elements with class 'hasevent' found in the HTML.")
    else:
        # # Print the CSV-like header
        print("Title, EventName, Link, ID")
        print("-" * 100)

        for td in td_elements:
            # Find all individual events within the <td>
            events = td.find_all("a", attrs={"data-action": "view-event"})
            if events:
                for event in events:
                    # TODO add mindaytimestamp (day 0 for sending tasks)
                    event_title = event.get("title", "No title available")
                    event_link = event.get("href", "No link available")
                    event_id = event.get("data-event-id", "No ID available")
                    event_name = event.find("span", class_="eventname")
                    event_name_text = event_name.get_text(strip=True) if event_name else "No event name"

                    # Print data in CSV-like format
                    print(f'"{event_title}", "{event_name_text}", "{event_link}", "{event_id}"')

                    # Insert data into the SQLite database
                    try:
                        cursor.execute("""
                        INSERT INTO events (event_id, title, event_name, link)
                        VALUES (?, ?, ?, ?)
                        """, (event_id, event_title, event_name_text, event_link))
                        print(f"Inserted into DB: {event_name_text}")
                    except sqlite3.IntegrityError:
                        print(f"Event ID {event_id} already exists in the database. Skipping...")

    # Commit the changes and close the connection
    conn.commit()
    conn.close()
    print("Database updated successfully.")

def extract_all_links(cookie_data):
    # **DATABASE CONNECTION**
    conn = sqlite3.connect("events.db")  # Change to MySQL/PostgreSQL if needed
    cursor = conn.cursor()

    # **Ensure column 'pdf_link' exists in 'events' table**
    cursor.execute("PRAGMA table_info(events)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "pdf_link" not in columns:
        cursor.execute("ALTER TABLE events ADD COLUMN pdf_link TEXT")
        print("✅ Added 'pdf_link' column to database.")
    
    # **Retrieve all links from 'events' table**
    cursor.execute("SELECT id, link FROM events")
    rows = cursor.fetchall()
    
    session = requests.Session()
    session.cookies.update({
        "MoodleSession": cookie_data['MoodleSession'],
        "MOODLEID1_": cookie_data['MOODLEID1_']
    })

    # **Regex pattern to match Moodle PDF URLs**
    pdf_pattern = re.compile(
        r"https?://moodle2\.e-wsb\.pl/pluginfile\.php/\d+/mod_assign/introattachment/\d+/elearning([1-9][0-9]?|100)\.pdf"
    )

    for event_id, panel_link in rows:
        try:
            res = session.get(panel_link) 
            soup = BeautifulSoup(res.text, "html.parser")

            # Find all <a> tags
            links = soup.find_all("a", href=True)
            pdf_link = None
            
            for link in links:
                href = link["href"]
                match = pdf_pattern.search(href)
                if match:
                    pdf_link = match.group(0)  # The URL without `?forcedownload=1`
                    break  # Stop after finding the first matching URL

            if pdf_link:
                print(f"Extracted PDF Link for Event {event_id}: {pdf_link}")

                # **Update the event record with extracted PDF link**
                cursor.execute(
                    "UPDATE events SET pdf_link = ? WHERE id = ?",
                    (pdf_link, event_id),
                )
                print(f"✅ Updated event {event_id} with PDF link.")

        except Exception as e:
            print(f"❌ Error processing event {event_id}: {e}")

    conn.commit()
    conn.close()
    print("✅ Database update process completed!")


def solve_pdf(pdf_link, cookie_data):
    print(f"solve_pdf({pdf_link})")
    session = requests.Session()
    session.cookies.update({
        "MoodleSession": cookie_data['MoodleSession'],
        "MOODLEID1_": cookie_data['MOODLEID1_']
    })
    response = session.get(pdf_link)

    if response.status_code == 200:
        pdf_stream = BytesIO(response.content)  # Store PDF in memory
        pdf_reader = PyPDF2.PdfReader(pdf_stream)

        # Extract text from all pages
        extracted_text = "\n".join([page.extract_text() for page in pdf_reader.pages if page.extract_text()])
        
        # print(extracted_text)  
    else:
        print("❌ Failed to access PDF. Check authentication or URL.")






    # Define the regex pattern to match "b) Zadania" (case-insensitive, with/without space)
    pattern = r"b\)\s*zadania(.*)"  # Matches 'b) Zadania' and captures everything after it
    match = re.search(pattern, extracted_text, re.IGNORECASE | re.DOTALL)  # Case-insensitive, multi-line

    if match:
        # Extract the text after "b) Zadania"
        extracted_text = match.group(1).strip()
        # print("Extracted Text:")
        # print(extracted_text)




        prompt = f"Extract each task from the following text, and return them as a numbered list: {extracted_text}"
        client = OpenAI(api_key=OPENAI_API_KEY)

        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": f"{prompt}",
                }
            ],
            model="gpt-3.5-turbo",
        )           
        text_response = chat_completion.choices[0].message.content


        solved_tasks = client.chat.completions.create(
            messages = [
                # {
                #     "role": "system",
                #     "content": "you are an engineer who always outputs solutions in a python file"
                # },
                {
                    "role": "user",
                    "content": f"{text_response} Opisz zadanie w komentarzu i pod spodem daj rozwiązanie WSZYSTKICH ZADAŃ W JEDNYM TERMINALU w kodzie python, przed pierwszym zadaniem i po ostatnim dodaj tekst KURWA, żebym mógł wyekstraktować tekst potem w re "
                }
            ],
            model="gpt-4o",
        )
        time.sleep(2)
        solutions_response = solved_tasks.choices[0].message.content
        time.sleep(2)
        # print(solutions_response)

        def extract_between_keywords(text, keyword):
            pattern = rf"{keyword}(.*?){keyword}"
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()
            return None

        # Extract content between KURWA
        extracted_content = extract_between_keywords(solutions_response, "KURWA")

        # Print the result
        if extracted_content:
            # print("Extracted Content:\n")
            # print(extracted_content)
            print(f"gpt-4o did the job!")
        else:
            print("No content found between the specified keywords.")



        def save_to_file(content, file_path):
            if content:
                with open(file_path, "w") as file:
                    file.write(content)
                print(f"Zapisano odpowiedzi do pliku {file_path}")
            else:
                print("No content to save, as nothing was extracted.")

        # Specify the file path
        pattern2 = r'elearning(\d+)\.pdf'
        match2 = re.search(pattern2, pdf_link)
        elearning_num = match2.group(1)
        file2_path = f"56058_etap{elearning_num}.py"
        save_to_file(extracted_content, file2_path)
        # print(f"saved as: {file2_path}")
        print('')
        print("*-"*45+"*")
        print('')



    else:
        print("Pattern 'b) Zadania' not found in the text.")

def solve_all_pdfs(cookie_data):

    # Connect to the SQLite database
    db_path = "events.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Fetch all pdf_link values
    cursor.execute("SELECT pdf_link FROM events")
    pdf_links = cursor.fetchall()

    # Loop through all rows and call solve_pdf
    for row in pdf_links:
        pdf_link = row[0]  # Extract the link from the tuple
        if pdf_link:  # Ensure it's not None or empty
            print(f"processing: {pdf_link}")
            solve_pdf(pdf_link, cookie_data)

    # Close the connection
    conn.close()



def upload_pdf(link, pdf_link, cookie_data):


    # TODO if pdf_link grab it here as pdf_link and grab its link as link
    print(cookie_data)
    session = requests.Session()
    session.cookies.update({
        "MoodleSession": cookie_data['MoodleSession'],
        "MOODLEID1_": cookie_data['MOODLEID1_']
    })


    pdf_pattern = r'elearning(\d+)\.pdf'
    pdf_match = re.search(pdf_pattern, pdf_link)
    elearning_num = pdf_match.group(1)
    file_name = f"56058_etap{elearning_num}.py"
    files = {

    "repo_upload_file": (file_name, open(file_name, "rb")),
    }


    itemid = f"0987654321{elearning_num}" # TODO might be adjusted to looks more realistic
    taskid_pattern = r'id=(\d+)'
    taskid_match = re.search(taskid_pattern, link)
    taskid = taskid_match.group(1)
    sesskey = cookie_data['sesskey']
    userid = cookie_data['userid']
    clientid = "678973ba90025" #TODO take dynamically from body response to login, find nowhere, assume its same for all users:?
    title = ""
    author = "Kamil Marszałkowski" #TODO could be dynamic
    repoid = 3
    # file to draft
    f2d_url = f"https://moodle2.e-wsb.pl/repository/repository_ajax.php"
    # draft to server
    d2s_url = f"https://moodle2.e-wsb.pl/mod/assign/view.php"

    f2d_payload = {
        "title": title,
        "author": author,
        "license": "allrightsreserved",
        "itemid": itemid,
        "repo_id": repoid,
        "p": "",
        "page": "",
        "env": "filemanager",
        "sesskey": sesskey,
        "client_id": clientid,
        "maxbytes": 104857600,
        "areamaxbytes": -1,
        "savepath": "/"
    }
    d2s_payload = {
        "files_filemanager": itemid,
        "id": taskid,
        "lastmodified": str(int(time.time())),
        "_qf__mod_assign_submission_form": "1",
        "action": "savesubmission",
        "userid": userid,
        "mform_isexpanded_id_submissionheader": "1",
        "submitbutton": "Zapisz+zmiany"
    }




    f2d_headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Accept": "*/*"
    }
    d2s_headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Accept": "*/*"
    }
    f2d_params = {
        "action": "upload"
    }
    d2s_params = {
        "sesskey": sesskey
    }


    # upload a draft
    f2d_res = session.post(f2d_url, params=f2d_params, headers=f2d_headers, data=f2d_payload, files=files)
    # save in db
    d2s_res = session.post(d2s_url, params=d2s_params, headers=d2s_headers, data=d2s_payload)

    print(f"{f2d_res.status_code} f2d status for {link}")
    print('')
    print(f"{d2s_res.status_code} d2s status for {link}")
    # print(d2s_res.text)
    time.sleep(5)

def upload_all_pdfs(cookie_data):
    db_path = "events.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    query = """
        SELECT pdf_link, link
        FROM events
        WHERE pdf_link IS NOT NULL;
        """
    cursor.execute(query)
    rows = cursor.fetchall()
    for row in rows:
        pdf_link, link = row
        print(f"Uploading: {link}")
        upload_pdf(link, pdf_link, cookie_data)
        time.sleep(5)

    conn.close()


def remove_pdf(link, cookie_data):

    session = requests.Session()
    session.cookies.update({
        "MoodleSession": cookie_data['MoodleSession'],
        "MOODLEID1_": cookie_data['MOODLEID1_']
    })


    taskid_pattern = r'id=(\d+)'
    taskid_match = re.search(taskid_pattern, link)
    taskid = taskid_match.group(1)
    sesskey = cookie_data['sesskey']
    userid = cookie_data['userid']
    url = "https://moodle2.e-wsb.pl/mod/assign/view.php"


    payload = {
        "id": taskid,
        "action": "removesubmission",
        "userid": userid,
        "sesskey": sesskey
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Accept": "*/*"
    }
    res = session.post(url, headers=headers, data=payload)
    # print(res.status_code)
    print(f"{res.status_code} for removing {link}")
    # time.sleep(5)

def remove_all_pdfs(cookie_data):
    db_path = "events.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    query = """
        SELECT DISTINCT link
        FROM events
        WHERE pdf_link IS NOT NULL;
        """
    cursor.execute(query)
    rows = cursor.fetchall()
    # print(rows)
    for row in rows:
        link = row[0]
        print(f"Removing: {link}")
        remove_pdf(link, cookie_data)
        time.sleep(5)

    conn.close()

def remove_draft(link, cookie_data):


    session = requests.Session()
    session.cookies.update({
        "MoodleSession": cookie_data['MoodleSession'],
        "MOODLEID1_": cookie_data['MOODLEID1_']
    })


    taskid_pattern = r'id=(\d+)'
    taskid_match = re.search(taskid_pattern, link)
    taskid = taskid_match.group(1)
    sesskey = cookie_data['sesskey']
    url = "https://moodle2.e-wsb.pl/mod/assign/view.php"


    payload = {
        "id": taskid,
        "action": "delete",
        "client_id":"6794daf95dc58",
        "sesskey": sesskey
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Accept": "*/*"
    }
    params = {
        "action":"delete"
    }

    res = session.post(url, headers=headers, data=payload)
    # print(res.status_code)
    print(f"{res.status_code} for removing {link}")
    # time.sleep(5)
    return 0



def main():
    
    # counter = 0
    # while counter < 3:
    #     print('')
    #     print(f"Try {counter+1} of 3")
    #     user_id = input("Enter index number: ")
    #     password = getpass.getpass("Enter password: ")
    #     print('')
    #     print("Wrong index number or password!")
    #     print('')
    #     print("*-"*50,"*")
    #     print('')
    #     counter += 1
    

    # print(f"User: dsw{user_id} logged in successfully!")
    # username = "dsw" + user_id
    # print('Retrieving session data in progress...')





    
    username = LOGIN
    password = PASSWORD


    cookie_data = capture_cookies_and_userid(username, password) # Returns dict cookie_data with userid, sesskey, moodleid, moodlesession
    print(f"Session ID: {cookie_data['MOODLEID1_']}")









    # TODO GET SCRAPED ALL THE MONTHS FROM CALENDAR
    # for i, (month, url) in enumerate(sem1.items(), start=1):
    #     print(''*5)
    #     extract_events_by_month(url, cookie_data)
    #     print(f"{i}: {month} extracted")

    # extract_all_links(cookie_data) 
    # solve_all_pdfs(cookie_data)

    # solve_pdf("https://moodle2.e-wsb.pl/pluginfile.php/11663623/mod_assign/introattachment/0/elearning5.pdf", cookie_data)


    # a = "https://moodle2.e-wsb.pl/mod/assign/view.php?id=10207020"
    # b = "https://moodle2.e-wsb.pl/pluginfile.php/11701556/mod_assign/introattachment/0/elearning10.pdf"
    
    # print(user_id)
    # upload_pdf(a, b, cookie_data)
    # upload_all_pdfs(cookie_data)
    # remove_pdf(a)
    remove_all_pdfs(cookie_data)


    # remove_draft(cookie_data) 














if __name__ == "__main__":
    main()







