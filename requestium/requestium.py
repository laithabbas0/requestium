import requests
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException
from parsel.selector import Selector
import time
import tldextract
from functools import partial


class Session(requests.Session):
    """Class that mixes Requests' Sessions, Selenium Webdriver, plus helper methods

    This session class is a normal Requests Session that has the ability to switch back
    and forth between this session and a webdriver, allowing us to run js when needed.

    Cookie transfer is done with the 'switch' methods.

    Header and proxy transfer is done only one time when the driver process starts.

    Some usefull helper methods and object wrappings have been added.
    """
    _driver = None
    _last_requests_url = None

    def __init__(self, webdriver_path='./phantomjs', default_timeout=5, browser='phantomjs'):
        super(Session, self).__init__()
        self.webdriver_path = webdriver_path
        self.default_timeout = default_timeout
        self.browser = browser

    @property
    def driver(self):
        if self._driver is None:
            if self.browser == 'phantomjs':
                self._start_phantomjs_browser()
            elif self.browser == 'chrome':
                self._start_chrome_browser()
            else:
                raise AttributeError(
                    'Browser must be chrome or phantomjs, not: "{}"'.format(self.browser)
                )

            # Add useful method to driver
            self.driver.ensure_element_by_xpath = self.__ensure_element_by_xpath
        return self._driver

    def _start_phantomjs_browser(self, webdriver_path):
        # Add headers to driver
        for key, value in self.headers.items():
            # Manually setting Accept-Encoding to anything breaks it for some reason
            if key == 'Accept-Encoding': continue

            webdriver.DesiredCapabilities.PHANTOMJS[
                'phantomjs.page.customHeaders.{}'.format(key)] = value

        # Set browser options
        service_args = ['--load-images=no', '--disk-cache=true']

        # Add proxies to driver
        if self.proxies:
            session_proxy = self.proxies['https'] or self.proxies['http']
            proxy_user_and_pass = session_proxy.split('@')[0][7:]
            proxy_ip_address = session_proxy.split('@')[1]
            service_args.append('--proxy=' + proxy_ip_address)
            service_args.append('--proxy-auth=' + proxy_user_and_pass)

        # Create driver process
        self._driver = webdriver.PhantomJS(executable_path=self.webdriver_path,
                                           service_log_path="/tmp/ghostdriver.log",
                                           service_args=service_args)

    def _start_chrome_browser(self):
        # TODO transfer headers, and authenticated proxyes: not sure how to do it in chrome

        chrome_options = webdriver.chrome.options.Options()

        # The infobar at the top saying 'Chrome is being controlled by an automated software'
        # sometimes hide elements from being clickable! So we disable it.
        chrome_options.add_argument('disable-infobars')


        # Create driver process
        self._driver = webdriver.Chrome(self.webdriver_path, chrome_options=chrome_options)

    def update_driver_cookies(self, url=None):
        """Copies the Session's cookies into the webdriver

        You can only transfer cookies to the driver if its current url is the same
        as the cookie's domain. This is a limitation that selenium imposes.
        """

        if url is None:
            url = self._last_requests_url

        # Check if the driver should go to a certain domain before transferring cookies
        # (Selenium and Requests prepend domains with an '.')
        driver_tld = self.get_tld(self.driver.current_url)
        new_request_tld = self.get_tld(url)
        if '.' + new_request_tld in self.cookies.list_domains() and driver_tld != new_request_tld:
            self.driver.get('http://' + self.get_tld(url))
            driver_tld = self.get_tld(self.driver.current_url)
            # assert driver_tld == new_request_tld, "{} != {}".format(driver_tld, new_request_tld)

        # Transfer cookies
        for c in self.cookies:
            self.driver.add_cookie({'name': c.name, 'value': c.value, 'path': c.path,
                                    'expiry': c.expires, 'domain': c.domain})

        self.driver.get(url)

    def update_session_cookies(self):
        for cookie in self.driver.get_cookies():
                self.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])

    def get_tld(self, url):
        """Return the top level domain

        If the registered domain could not be extracted, assume that it's simply an IP and
        strip away the protocol prefix and potentially trailing rest after "/" away.
        If it isn't, this fails gracefully for unknown domains, e.g.:
           "http://domain.onion/" -> "domain.onion". If it doesn't look like a valid address
        at all, return the URL unchanged.
        """
        components = tldextract.extract(url)
        if not components.registered_domain:
            try:
                return url.split('://', 1)[1].split(':', 1)[0].split('/', 1)[0]
            except IndexError:
                return url
        return components.registered_domain

    def __ensure_element_by_xpath(self, selector, criterium="presence", timeout=None):
        """This method allows us to wait till an element is loaded in selenium

        This method is added to the driver object. And its more robust than any of Selenium's
        default options for waiting.

        Selenium runs in parallel with our scripts, so we must wait for it everytime it
        runs javascript. Selenium automatically makes our python scripts when its GETing
        a new webpage, but it doesnt do this when it runs javascript and makes AJAX requests.
        So we must explicitly wait in this case.

        The 'criterium' parameter allows us to chose between the visibility and presence of
        the item in the webpage. Presence is more inclusive, but sometimes we want to know if
        the element is visible. Careful, its not always intuitive what Selenium considers to be
        a visible element.

        This is a barebones implementation, which only supports xpath. It could be usefull to
        add more filters in the future, a comprehensive list of the possible filters can be
        found here: http://selenium-python.readthedocs.io/waits.html

        This function returns the element its waiting for, so it could actually replace
        the default selenium method 'find_element_by_xpath'. I am not doing it for the time being
        as it could cause confusion and have some adverse effects I may not not be aware of,
        but its worth considering doing it when this whole library is more stable and we have a
        better defined api.

        This function also scrolls the element into view before returning it, so we can ensure that
        the element is clickable before returning it.
        """
        type = By.XPATH
        if not timeout: timeout = self.default_timeout

        if criterium == 'visibility':
            element = WebDriverWait(self._driver, timeout).until(
                EC.visibility_of_element_located((type, selector))
            )
        elif criterium == 'clickable':
            element = WebDriverWait(self._driver, timeout).until(
                EC.element_to_be_clickable((type, selector))
            )
        elif criterium == 'presence':
            element = WebDriverWait(self._driver, timeout).until(
                EC.presence_of_element_located((type, selector))
            )
        else:
            raise ValueError(
                "The 'criterium' argument must be 'visibility', 'clickable' "
                "or 'presence', not '{}'".format(criterium)
            )

        # This next method returns the location of an element once its scrolled into view.
        # It scrolls the element into view first though, so its an effective way to ensure
        # the element is viewable when we return it in case we want to click it.
        element.location_once_scrolled_into_view

        # We add this method to our element to provide a more robust click. Chromedriver
        # sometimes needs some time before it can click an item, specially if it needs to
        # scroll into it first. This method ensures clicks don't fail because of this.
        element.ensure_click = partial(_ensure_click, element)
        return element

    def get(self, *args, **kwargs):
        resp = super(Session, self).get(*args, **kwargs)
        self._last_requests_url = resp.url
        return RequestiumResponse(resp)

    def post(self, *args, **kwargs):
        resp = super(Session, self).post(*args, **kwargs)
        self._last_requests_url = resp.url
        return RequestiumResponse(resp)

    def put(self, *args, **kwargs):
        resp = super(Session, self).put(*args, **kwargs)
        self._last_requests_url = resp.url
        return RequestiumResponse(resp)


class RequestiumResponse(object):
    """Adds xpath, css, and regex methods to a normal requests response object"""

    def __init__(self, response):
        self.__class__ = type(response.__class__.__name__,
                              (self.__class__, response.__class__),
                              response.__dict__)
        # self.__dict__ = response.__dict__  # TODO delete?
        self.response = response
        self._selector = None

    @property
    def selector(self):
        if self._selector is None:
            self._selector = Selector(text=self.response.text)
        return self._selector

    def xpath(self, *args, **kwargs):
        return self.selector.xpath(*args, **kwargs)

    def css(self, *args, **kwargs):
        return self.selector.css(*args, **kwargs)

    def re(self, *args, **kwargs):
        return self.selector.re(*args, **kwargs)

    def re_first(self, *args, **kwargs):
        return self.selector.re_first(*args, **kwargs)


def _ensure_click(self):
    """Ensures a click gets made even when using the buggy chromedriver

    This method gets added to the selenium elemenent returned in '__ensure_element_by_xpath'.

    I wrote this method out of frustration with chromedriver and its problems with clicking
    items that need to be scrolled to in order to be clickable. In '__ensure_element_by_xpath' we
    scroll to the item before returning it, but chrome has some problems if it doesn't get some
    time to scroll to the item. This method ensures chromes gets enough time to scroll to the item
    before clicking it. I tried SEVERAL more 'correct' methods to get around this, but none of them
    worked 100% of the time. Checking if the item is 'clickable' does not work.
    """
    for _ in range(5):
        try:
            self.click()
            return
        except WebDriverException as e:
            time.sleep(0.2)
    raise WebDriverException(
        "Couldn't click item after trying 5 times, got error message: \n{}".format(e.message)
    )
