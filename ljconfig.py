lj_server = "https://livejournal.com"
username = "ljusername"
password = "ljpassword"

# start and end dates are in YYYY/MM/DD format
# Entries are downloaded in one-month chunks at a minimum
start_date = "2003/07/01"
end_date = "2018/07/31"

# Header is used only for making the requests to the LJ server; it is arbitrary
header = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 8.1; rv:10.0) Gecko/20100101 Firefox/10.0'
}
