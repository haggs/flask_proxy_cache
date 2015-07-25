#########################################################################################################
# This is our really simple cache class. It's more-or-less a key-value store of URL's to GET responses.
#########################################################################################################
from flask import request
import requests
from urlparse import urlparse
from datetime import datetime
import pytz
import sys

class ResponseCache:

    # Instantiate member variables, validate configuration
    def __init__(self, cache_duration_ms = 30 * 1000, cache_size_bytes = 1024, cache_size_elements = 100, log_table_max_size = 50, logger=None):
        self.cache_duration_ms   = cache_duration_ms
        self.cache_size_bytes    = cache_size_bytes
        self.cache_size_elements = cache_size_elements
        self.log_table_max_size  = log_table_max_size
        self.cache_dict = {}
        self.logger = logger
        self.log = []
        self.log_message("Instantiated ResponseCache")
        self.validate_configuration(cache_duration_ms, cache_size_bytes, cache_size_elements, log_table_max_size)

    # Log a message to logger, and to our log table seen in localhost:PORT/proxyinfo
    def log_message(self, msg, level="INFO"):
        if self.logger:
            if level == "ERROR":
                self.logger.error(msg)
            elif level == "WARNING":
                self.logger.warning(msg)
            else:
                self.logger.info(msg)
        if len(self.log) + 1 > self.log_table_max_size:
            self.log.pop()
        self.log.insert(0, msg)

    # Basic method overloads to use ResponseCache like a container
    def __contains__(self, key):
        return key in self.cache_dict

    def __getitem__(self, item):
        return self.cache_dict[item]

    def __iter__(self):
        return iter(self.cache_dict)

    def length(self):
        return len(self.cache_dict)

    def get_time(self, url):
        return self.cache_dict[url]['last_updated'].strftime("%Y-%m-%d %H:%M:%S %Z")

    # Gets the size of a response, which is represented as a list of elements of 1024 Bytes
    def get_size(self, url):
        return sys.getsizeof(self.cache_dict[url]['response'])

    def get_total_size(self):
        size = 0
        for url in self.cache_dict:
            size += self.get_size(url)
        return size

    # If a cache entry is older than CACHE_DURATION_MS in conf.py, return True
    def cache_expired(self, url):
        age_ms = datetime.now(pytz.timezone("US/Pacific")) - self.cache_dict[url]['last_updated']
        if age_ms.seconds * 1000 + age_ms.microseconds / 1000 > self.cache_duration_ms:
            return True
        return False

    # Delete the oldest cache entry
    def delete_oldest(self):
        oldest = datetime.now(pytz.timezone("US/Pacific"))
        for url in self.cache_dict:
            if self.cache_dict[url]['last_updated'] < oldest:
                self.delete(url)
                break

    def insert(self, url):

        # If adding this element puts us over our cache size (elements) limit, delete this oldest record
        if len(self.cache_dict) + 1 > self.cache_size_elements:
            self.log_message("Reached cache element limit, deleting oldest record")
            self.delete_oldest()

        # Fetch the URL. What we're actually caching is the request content as a list of elements of size 1 KB
        proxy_ref = self.parse_referer_info(request)
        headers = { "Referer" : "http://%s/%s" % (proxy_ref[0], proxy_ref[1])} if proxy_ref else {}
        req = requests.get(url, stream=True , params = request.args, headers=headers)
        response = list(req.iter_content(1024))
        response_size = sys.getsizeof(response)

        # If the size of the response is bigger than the cache size in the configuration file, throw an error
        if response_size > self.cache_size_bytes:
            msg = "Received response of size: " + str(response_size) + "B but the maximum cache size is " \
                  + str(self.cache_size_bytes) + "B. You should up the size of CACHE_SIZE_BYTES in conf.py"
            self.log_message(msg)
            raise Exception(msg)

        # If adding this element puts us over our cache size (Bytes) limit, keep deleting the oldest
        # record until we have space for it
        while self.get_total_size() + sys.getsizeof(response) > self.cache_size_bytes:
            self.log_message("Reached cache size Byte limit, deleting oldest record(s)")
            self.delete_oldest()

        # Finally add the response, its headers, and the time we updated the record to the internal cache dictionary.
        self.cache_dict[url] = { "response" : response,
                                 "headers"  : dict(req.headers),
                                 "last_updated" : datetime.now(pytz.timezone("US/Pacific"))
                               }

    # Delete the URL from the internal cache dictionary
    def delete(self, url):
        del self.cache_dict[url]

    # Fetches the specified URL and streams it out to the client.
    # If the request was referred by the proxy itself (e.g. this is an image fetch for
    # a previously proxied HTML page), then the original Referer is passed.
    def get(self, url):
        url = 'http://' + url
        # LOG.info("Fetching %s", url)
        # Pass original Referer for subsequent resource requests

        # Fetch the URL, and stream it back
        # LOG.info("Fetching with headers: %s, %s", url, headers)
        if url not in self.cache_dict:
            self.log_message("URL doesn't exist in cache, inserting: " + url)
            self.insert(url)

        elif self.cache_expired(url):
            self.log_message("URL exists in cache but is stale, fetching and caching: " + url)
            self.delete(url)
            self.insert(url)

        else:
            self.log_message("URL exists in cache and is fresh:" + url)
        return self.cache_dict[url]['headers'], self.cache_dict[url]['response']


    def parse_referer_info(self, request):
        ref = request.headers.get('referer')
        if ref:
            uri = urlparse(ref).path

            if uri.find("/") < 0:
                return None
            uri_split = uri.split("/", 2)
            if uri_split[1] in "proxyd":
                parts = uri_split[2].split("/", 1)
                r = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")
                return r
        return None

    # Error checking and warnings for the configuration file
    def validate_configuration(self, cache_duration_ms, cache_size_bytes, cache_size_elements, log_table_max_size):

        if cache_duration_ms <= 0:
            msg = "ResponseCache: CACHE_DURATION_MS must be greater than 0"
            raise Exception(msg)
        if cache_duration_ms < 1000:
            self.log_message("CACHE_DURATION_MS is less than 1000ms. You might want to set a longer duration", "WARNING")

        if cache_size_bytes <= 0:
            raise Exception("ResponseCache: CACHE_SIZE_BYTES must be greater than 0")
        if cache_size_bytes < 2048:
            self.log_message("CACHE_SIZE_BYTES is less than 2KB. You might want to set a larger cache size", "WARNING")

        if cache_size_elements <= 0:
            raise Exception("ResponseCache: CACHE_SIZE_ELEMENTS must be greater than 0")
        if cache_size_elements < 8:
            self.log_message("CACHE_SIZE_ELEMENTS is less than 8. You might want to set a larger cache size", "WARNING")

        if log_table_max_size < 0:
            raise Exception("ResponseCache: LOG_TABLE_MAX_SIZE")
        if log_table_max_size < 2:
            self.log_message("LOG_TABLE_MAX_SIZE is less than 2. You won't be able to see much info", "WARNING")

        self.log_message("Validated conf.py")
