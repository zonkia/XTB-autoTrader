import json
import socket
import logging
import time
import ssl
from threading import Thread
import os
from datetime import datetime
from datetime import timedelta
from pprint import pprint
import math
import requests
import eventlet
from copy import deepcopy
from collections import Counter

os.chdir(os.path.dirname(__file__))

# set to true on debug environment only
DEBUG = True

# default connection properites
DEFAULT_XAPI_ADDRESS = 'xapi.xtb.com'
DEFAULT_XAPI_PORT = 5124
DEFUALT_XAPI_STREAMING_PORT = 5125

# API inter-command timeout (in ms)
API_SEND_TIMEOUT = 500

# max connection tries
API_MAX_CONN_TRIES = 4

# logger properties
logger = logging.getLogger("jsonSocket")
FORMAT = '[%(asctime)-15s][%(funcName)s:%(lineno)d] %(message)s'
logging.basicConfig(format=FORMAT, level=logging.DEBUG,
                    filename='error_logs.log')

if DEBUG:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.CRITICAL)


def reverse_dict(dictionary):
    reversed_keys = reversed(list(dictionary.keys()))
    return {key: dictionary[key]
            for key in reversed_keys
            }


def truncate(number, digits) -> float:
    stepper = 10.0 ** digits
    return math.trunc(stepper * number) / stepper


class TransactionSide(object):
    BUY = 0
    SELL = 1
    BUY_LIMIT = 2
    SELL_LIMIT = 3
    BUY_STOP = 4
    SELL_STOP = 5


class TransactionType(object):
    ORDER_OPEN = 0
    ORDER_CLOSE = 2
    ORDER_MODIFY = 3
    ORDER_DELETE = 4


class JsonSocket(object):
    def __init__(self, address, port, encrypt=False):
        self._ssl = encrypt
        if self._ssl != True:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket = ssl.wrap_socket(sock)
        self.conn = self.socket
        self._timeout = None
        self._address = address
        self._port = port
        self._decoder = json.JSONDecoder()
        self._receivedData = ''

    def connect(self):
        for _ in range(API_MAX_CONN_TRIES):
            try:
                self.socket.connect((self.address, self.port))
                self.socket.settimeout(6)
            except socket.error as msg:
                logger.error("SockThread Error: %s" % msg)
                time.sleep(1)
                continue
            # logger.info("Socket connected")
            return True
        return False

    def _sendObj(self, obj):
        msg = json.dumps(obj)
        self._waitingSend(msg)

    def _waitingSend(self, msg):
        if self.socket:
            sent = 0
            msg = msg.encode('utf-8')
            while sent < len(msg):
                sent += self.conn.send(msg[sent:])
                # logger.info('Sent: ' + str(msg))
                time.sleep(API_SEND_TIMEOUT/1000)

    def _read(self, bytesSize=4096):
        if not self.socket:
            raise RuntimeError("socket connection broken")
        while True:
            char = self.conn.recv(bytesSize).decode()
            self._receivedData += char
            try:
                (resp, size) = self._decoder.raw_decode(self._receivedData)
                if size == len(self._receivedData):
                    self._receivedData = ''
                    break
                elif size < len(self._receivedData):
                    self._receivedData = self._receivedData[size:].strip()
                    break
            except ValueError as e:
                continue
        # logger.info('Received: ' + str(resp))
        return resp

    def _readObj(self):
        msg = self._read()
        return msg

    def close(self):
        # logger.debug("Closing socket")
        self._closeSocket()
        if self.socket is not self.conn:
            # logger.debug("Closing connection socket")
            self._closeConnection()

    def _closeSocket(self):
        self.socket.close()

    def _closeConnection(self):
        self.conn.close()

    def _get_timeout(self):
        return self._timeout

    def _set_timeout(self, timeout):
        self._timeout = timeout
        self.socket.settimeout(timeout)

    def _get_address(self):
        return self._address

    def _set_address(self, address):
        pass

    def _get_port(self):
        return self._port

    def _set_port(self, port):
        pass

    def _get_encrypt(self):
        return self._ssl

    def _set_encrypt(self, encrypt):
        pass

    timeout = property(_get_timeout, _set_timeout,
                       doc='Get/set the socket timeout')
    address = property(_get_address, _set_address,
                       doc='read only property socket address')
    port = property(_get_port, _set_port, doc='read only property socket port')
    encrypt = property(_get_encrypt, _set_encrypt,
                       doc='read only property socket port')


class APIClient(JsonSocket):
    def __init__(self, address=DEFAULT_XAPI_ADDRESS, port=DEFAULT_XAPI_PORT, encrypt=True):
        super(APIClient, self).__init__(address, port, encrypt)
        if(not self.connect()):
            raise Exception("Cannot connect to " + address + ":" +
                            str(port) + " after " + str(API_MAX_CONN_TRIES) + " retries")

    def execute(self, dictionary):
        self._sendObj(dictionary)
        return self._readObj()

    def disconnect(self):
        self.close()

    def commandExecute(self, commandName, arguments=None):
        return self.execute(baseCommand(commandName, arguments))


class APIStreamClient(JsonSocket):
    def __init__(self, address=DEFAULT_XAPI_ADDRESS, port=DEFUALT_XAPI_STREAMING_PORT, encrypt=True, ssId=None,
                 tickFun=None, tradeFun=None, balanceFun=None, tradeStatusFun=None, profitFun=None, newsFun=None):
        super(APIStreamClient, self).__init__(address, port, encrypt)
        self._ssId = ssId

        self._tickFun = tickFun
        self._tradeFun = tradeFun
        self._balanceFun = balanceFun
        self._tradeStatusFun = tradeStatusFun
        self._profitFun = profitFun
        self._newsFun = newsFun

        if(not self.connect()):
            raise Exception("Cannot connect to streaming on " + address + ":" +
                            str(port) + " after " + str(API_MAX_CONN_TRIES) + " retries")

        self._running = True
        self._t = Thread(target=self._readStream, args=())
        self._t.setDaemon(True)
        self._t.start()

    def _readStream(self):
        while (self._running):
            msg = self._readObj()
            # logger.info("Stream received: " + str(msg))
            if (msg["command"] == 'tickPrices'):
                self._tickFun(msg)
            elif (msg["command"] == 'trade'):
                self._tradeFun(msg)
            elif (msg["command"] == "balance"):
                self._balanceFun(msg)
            elif (msg["command"] == "tradeStatus"):
                self._tradeStatusFun(msg)
            elif (msg["command"] == "profit"):
                self._profitFun(msg)
            elif (msg["command"] == "news"):
                self._newsFun(msg)

    def disconnect(self):
        self._running = False
        self._t.join()
        self.close()

    def execute(self, dictionary):
        self._sendObj(dictionary)

    def subscribePrice(self, symbol):
        self.execute(dict(command='getTickPrices',
                          symbol=symbol, streamSessionId=self._ssId))

    def subscribePrices(self, symbols):
        for symbolX in symbols:
            self.subscribePrice(symbolX)

    def subscribeTrades(self):
        self.execute(dict(command='getTrades', streamSessionId=self._ssId))

    def subscribeBalance(self):
        self.execute(dict(command='getBalance', streamSessionId=self._ssId))

    def subscribeTradeStatus(self):
        self.execute(dict(command='getTradeStatus',
                          streamSessionId=self._ssId))

    def subscribeProfits(self):
        self.execute(dict(command='getProfits', streamSessionId=self._ssId))

    def subscribeNews(self):
        self.execute(dict(command='getNews', streamSessionId=self._ssId))

    def unsubscribePrice(self, symbol):
        self.execute(dict(command='stopTickPrices',
                          symbol=symbol, streamSessionId=self._ssId))

    def unsubscribePrices(self, symbols):
        for symbolX in symbols:
            self.unsubscribePrice(symbolX)

    def unsubscribeTrades(self):
        self.execute(dict(command='stopTrades', streamSessionId=self._ssId))

    def unsubscribeBalance(self):
        self.execute(dict(command='stopBalance', streamSessionId=self._ssId))

    def unsubscribeTradeStatus(self):
        self.execute(dict(command='stopTradeStatus',
                          streamSessionId=self._ssId))

    def unsubscribeProfits(self):
        self.execute(dict(command='stopProfits', streamSessionId=self._ssId))

    def unsubscribeNews(self):
        self.execute(dict(command='stopNews', streamSessionId=self._ssId))


# Command templates
def baseCommand(commandName, arguments=None):
    if arguments == None:
        arguments = dict()
    return dict([('command', commandName), ('arguments', arguments)])


def loginCommand(userId, password, appName=''):
    return baseCommand('login', dict(userId=userId, password=password, appName=appName))


# example function for processing ticks from Streaming socket
def procTickExample(msg):
    print("TICK: ", msg)

# example function for processing trades from Streaming socket


def procTradeExample(msg):
    print("TRADE: ", msg)

# example function for processing trades from Streaming socket


def procBalanceExample(msg):
    print("BALANCE: ", msg)

# example function for processing trades from Streaming socket


def procTradeStatusExample(msg):
    print("TRADE STATUS: ", msg)

# example function for processing trades from Streaming socket


def procProfitExample(msg):
    print("PROFIT: ", msg)

# example function for processing news from Streaming socket


def procNewsExample(msg):
    print("NEWS: ", msg)

# ---------------------------------------------------------------------


def save_actual_calendar_json(client):
    calendarFromApi = client.commandExecute('getCalendar')

    argument = {
        "symbol": "EURPLN"
    }

    fullCalendar = []
    index = 0
    for entryDict in calendarFromApi["returnData"]:
        if entryDict["impact"] == "3" or entryDict["impact"] == "2":
            fullCalendar.append(entryDict)
            fullCalendar[index]["time"] = str(datetime.fromtimestamp(
                entryDict["time"] / 1000))
            index += 1

    with open("fullCalendar.json", "w", encoding="UTF-8-sig") as fileJson:
        json.dump(fullCalendar, fileJson, ensure_ascii=False, indent=4)


def get_important_calendar_events_dict(client, importantCountriesList):
    calendarFromApi = client.commandExecute('getCalendar')

    fullCalendar = []
    index = 0
    for entryDict in calendarFromApi["returnData"]:
        if (entryDict["country"] in importantCountriesList) and (entryDict["impact"] == "3" or entryDict["impact"] == "2"):
            fullCalendar.append(entryDict)
            fullCalendar[index]["time"] = str(datetime.fromtimestamp(
                entryDict["time"] / 1000))
            index += 1
    return fullCalendar


class Calendar:

    def __init__(self, client, importantCurrencies):

        self.importantCurrencies = importantCurrencies

        self.fullOnlyImportantCalendar = Calendar.get_important_calendar_events_dict(
            self, client)

        onlyAnnouncementsTimes = [event["time"].split(" ")[1]
                                  for event in self.fullOnlyImportantCalendar
                                  ]

        self.onlyAnnouncementsTimesSorted = sorted(
            list(set(onlyAnnouncementsTimes)), reverse=True)

        self.currentTitlesDict = {event["title"]: ""
                                  for event in self.fullOnlyImportantCalendar
                                  }

    def get_important_calendar_events_dict(self, client):
        calendarFromApi = client.commandExecute('getCalendar')

        with open("countriesCurrencies.json", "r", encoding="UTF-8-sig") as countriesCurrencies:
            countriesCurrencies = json.load(countriesCurrencies)

        importantCountriesList = [country
                                  for country in countriesCurrencies
                                  if countriesCurrencies[country] in self.importantCurrencies
                                  ]
        # get today and tommorow timestamps

        timeDiff = timedelta(hours=3)
        startTime = (
            datetime.now() - timeDiff).strftime("%Y-%m-%d %H:%M:%S")

        # tymczasowo, żeby cokolwiek było w kalendarzu
        # startTime = str(datetime.today().date()) + " 00:00:00"
        dateDiff = timedelta(days=1)
        endTime = str((datetime.now() + dateDiff).date()) + " 23:59:59"

        self.startDateTimestamp = int(datetime.timestamp(
            datetime.strptime(startTime, '%Y-%m-%d %H:%M:%S')))*1000
        self.endDateTimestamp = int(datetime.timestamp(
            datetime.strptime(endTime, '%Y-%m-%d %H:%M:%S')))*1000
        # get only important events from today calendar
        fullCalendar = []
        index = 0
        for entryDict in calendarFromApi["returnData"]:
            if (entryDict["country"] in importantCountriesList) and \
                (entryDict["time"] >= self.startDateTimestamp) and \
                    (entryDict["time"] < self.endDateTimestamp) and \
                    (entryDict["impact"] == "3" or entryDict["impact"] == "2"):
                fullCalendar.append(entryDict)
                fullCalendar[index]["time"] = str(datetime.fromtimestamp(
                    entryDict["time"] / 1000))
                index += 1
            if entryDict["forecast"] == "":
                entryDict["forecast"] = entryDict["previous"]

        return fullCalendar

    def save_actual_calendar_json(self):

        with open("fullCalendar2.json", "w", encoding="UTF-8-sig") as fileJson:
            json.dump(self.fullOnlyImportantCalendar,
                      fileJson, ensure_ascii=False, indent=4)


class FileSupport:

    @staticmethod
    def read_file(name):
        with open(name + ".json", "r", encoding="UTF-8-sig") as jsonFile:
            return json.load(jsonFile)

    @staticmethod
    def save_file(name, dictionary, directory=""):

        with open(directory + name + ".json", "w", encoding="UTF-8-sig") as jsonFile:
            json.dump(dictionary, jsonFile, ensure_ascii=False, indent=4)


class BullsAndBears:

    def __init__(self, calendarFromApi):

        self.importantWithCurrentValue = [event
                                          for event in calendarFromApi.fullOnlyImportantCalendar
                                          if event["current"] != ""
                                          ]

        titlesDictionaryDirections = FileSupport.read_file(
            "titlesDictionaryDirections")
        # check for new titles
        newTitlesDict = {title: "Update Needed"
                         for title in calendarFromApi.currentTitlesDict
                         if title not in titlesDictionaryDirections
                         }
        FileSupport.save_file("newTitles", newTitlesDict)

        # merge new titles with older file, save json
        updatedTitlesDirections = {
            **titlesDictionaryDirections, **newTitlesDict}
        FileSupport.save_file("titlesDictionaryDirections",
                              updatedTitlesDirections)
        titlesDictionaryDirections = updatedTitlesDirections

        countriesCurrencies = FileSupport.read_file("countriesCurrencies")

        self.bullsCurrencies = [countriesCurrencies[eventWithCurrent["country"]]
                                for eventWithCurrent in self.importantWithCurrentValue
                                if (titlesDictionaryDirections[eventWithCurrent["title"]] == "Better Up" and
                                    (float(eventWithCurrent["current"]) - BullsAndBears.change_to_zeros(self, eventWithCurrent["forecast"]) > 0)) or
                                (titlesDictionaryDirections[eventWithCurrent["title"]] == "Better Down" and
                                 (float(eventWithCurrent["current"]) - BullsAndBears.change_to_zeros(self, eventWithCurrent["forecast"]) < 0)) or
                                BullsAndBears.check_minimum_value_bull(
                                    self, eventWithCurrent["title"], eventWithCurrent["current"])

                                ]

        self.bearsCurrencies = [countriesCurrencies[eventWithCurrent["country"]]
                                for eventWithCurrent in self.importantWithCurrentValue
                                if (titlesDictionaryDirections[eventWithCurrent["title"]] == "Better Up" and
                                    (float(eventWithCurrent["current"]) - BullsAndBears.change_to_zeros(self, eventWithCurrent["forecast"]) < 0)) or
                                (titlesDictionaryDirections[eventWithCurrent["title"]] == "Better Down" and
                                 (float(eventWithCurrent["current"]) - BullsAndBears.change_to_zeros(self, eventWithCurrent["forecast"]) > 0)) or
                                BullsAndBears.check_minimum_value_bear(
                                    self, eventWithCurrent["title"], eventWithCurrent["current"])

                                ]

        self.bearCurrenciesUnique = [currency
                                     for currency in list(set(self.bearsCurrencies))
                                     if currency not in list(set(self.bullsCurrencies))
                                     ]

        self.bullCurrenciesUnique = [currency
                                     for currency in list(set(self.bullsCurrencies))
                                     if currency not in list(set(self.bearsCurrencies))
                                     ]

    @staticmethod
    def change_trades_order(semiTrades, fullTrades, withoutCurrent):

        finalOrderTrades = {}
        orderAfterSemi = {}
        orderAfterFull = {}

        if len(list(semiTrades.keys())) > 0:
            for pair in reverse_dict(semiTrades):
                if pair in withoutCurrent:
                    orderAfterSemi[pair] = withoutCurrent[pair]

        if len(list(fullTrades.keys())) > 0:
            for pair in reverse_dict(fullTrades):
                if pair in withoutCurrent:
                    orderAfterFull[pair] = withoutCurrent[pair]

        finalOrderTrades = {**orderAfterFull, **orderAfterSemi}

        for pair in withoutCurrent:
            if pair not in finalOrderTrades:
                finalOrderTrades[pair] = withoutCurrent[pair]

        return finalOrderTrades

    def change_to_zeros(self, string):
        if string == "":
            return 0
        else:
            return float(string)

    def check_minimum_value_bull(self, key, comparedValue):
        with open("titlesDictionaryMinimums.json", "r", encoding="UTF-8-sig") as jsonFile:
            titlesDictionaryMinimums = json.load(jsonFile)

        try:
            if float(titlesDictionaryMinimums[key]) < float(comparedValue):
                return True
        except:
            return False

    def check_minimum_value_bear(self, key, comparedValue):
        with open("titlesDictionaryMinimums.json", "r", encoding="UTF-8-sig") as jsonFile:
            titlesDictionaryMinimums = json.load(jsonFile)

        try:
            if float(titlesDictionaryMinimums[key]) > float(comparedValue):
                return True
        except:
            return False

    def get_possible_trades_both_lists(self, possibleCurrencyPairs):

        allCombinations = []
        listToMakeCombinations = self.bearCurrenciesUnique + \
            self.bullCurrenciesUnique

        for currency in listToMakeCombinations:
            for currencyIndex in range(len(listToMakeCombinations)):
                currentCombination = str(currency) + \
                    str(listToMakeCombinations[currencyIndex])
                if currentCombination in possibleCurrencyPairs:
                    allCombinations.append(currentCombination)
        possibleFullTrades = list(set(allCombinations))

        # nadanie kierunku trejdom na podstawie kalendarza
        return BullsAndBears.get_trade_directions_for_pairs(self, possibleFullTrades)

    def get_semi_possible_trades(self, possibleCurrencyPairs, importantCurrencies):
        allCombinations = []
        listToMakeCombinations = self.bearCurrenciesUnique + \
            self.bullCurrenciesUnique

        for possibleCurrency in listToMakeCombinations:
            for currency in importantCurrencies:
                currentCombination = str(possibleCurrency) + str(currency)
                if (currentCombination in possibleCurrencyPairs) and (possibleCurrency in currentCombination):
                    allCombinations.append(currentCombination)
                alternativeCombination = str(currency) + str(possibleCurrency)
                if (alternativeCombination in possibleCurrencyPairs) and (possibleCurrency in alternativeCombination):
                    allCombinations.append(alternativeCombination)
        possibleSemiTrades = list(set(allCombinations))

        # nadanie kierunku trejdom na podstawie kalendarza
        return BullsAndBears.get_trade_directions_for_pairs(self, possibleSemiTrades)

    @staticmethod
    def get_possible_pairs_from_currencies_list(currenciesList, possibleCurrencyPairs, importantCurrencies):
        allCombinations = []
        listToMakeCombinations = currenciesList

        for possibleCurrency in listToMakeCombinations:
            for currency in importantCurrencies:
                currentCombination = str(possibleCurrency) + str(currency)
                if (currentCombination in possibleCurrencyPairs) and (possibleCurrency in currentCombination):
                    allCombinations.append(currentCombination)
                alternativeCombination = str(currency) + str(possibleCurrency)
                if (alternativeCombination in possibleCurrencyPairs) and (possibleCurrency in alternativeCombination):
                    allCombinations.append(alternativeCombination)
        uniquePairs = []
        for pair in allCombinations:
            if pair not in uniquePairs:
                uniquePairs.append(pair)
        return uniquePairs

    def get_unique_currency_pairs(self, pairsList):

        if len(list(pairsList.keys())) > 1:

            firstCurrenciesFromPairs = []
            secondCurrenciesFromPairs = []
            finalPairs = {}
            for pair in pairsList:
                firstCurrenciesFromPairs.append(pair[0:3])
                secondCurrenciesFromPairs.append(pair[3:6])
                allCurencies = firstCurrenciesFromPairs + secondCurrenciesFromPairs

                matchesDict = dict(Counter(allCurencies))

                if max(list(matchesDict.values())) < 2:
                    finalPairs[pair] = pairsList[pair]
                else:
                    firstCurrenciesFromPairs.pop()
                    secondCurrenciesFromPairs.pop()
                    allCurencies = firstCurrenciesFromPairs + secondCurrenciesFromPairs

            return finalPairs
        else:
            return pairsList

    def get_trade_directions_for_pairs(self, pairsList):

        if self.bullCurrenciesUnique == [] and \
                self.bearCurrenciesUnique == []:

            bothDict = {pair: "both"
                        for pair in pairsList
                        }
            return bothDict

        else:
            # sprawdzenie listy bull
            bullDict = {}
            for pair in pairsList:
                for bullCurrency in self.bullCurrenciesUnique:
                    if pair[3:6] in self.bullCurrenciesUnique and \
                            pair[0:3] in self.bullCurrenciesUnique:
                        pass
                    elif pair[3:6] == bullCurrency:
                        bullDict[pair] = "sell"
                    elif pair[0:3] == bullCurrency:
                        bullDict[pair] = "buy"
                    elif pair[3:6] not in self.bearCurrenciesUnique and \
                            pair[0:3] not in self.bearCurrenciesUnique and \
                            pair[3:6] not in self.bullCurrenciesUnique and \
                            pair[0:3] not in self.bullCurrenciesUnique:
                        bullDict[pair] = "both"

            # sprawdzenie listy bear
            bearDict = {}
            for pair in pairsList:
                for bearCurrency in self.bearCurrenciesUnique:
                    if pair[3:6] in self.bearCurrenciesUnique and \
                            pair[0:3] in self.bearCurrenciesUnique:
                        pass
                    elif pair[3:6] == bearCurrency:
                        bearDict[pair] = "buy"
                    elif pair[0:3] == bearCurrency:
                        bearDict[pair] = "sell"
                    elif pair[3:6] not in self.bearCurrenciesUnique and \
                            pair[0:3] not in self.bearCurrenciesUnique and \
                            pair[3:6] not in self.bullCurrenciesUnique and \
                            pair[0:3] not in self.bullCurrenciesUnique:
                        bearDict[pair] = "both"

            return {**bullDict, **bearDict}

    @staticmethod
    def split_pairs_to_currencies(pairsList):
        firstCurrenciesFromPairs = [pair[0:3]
                                    for pair in pairsList
                                    ]

        secondCurrenciesFromPairs = [pair[3:6]
                                     for pair in pairsList
                                     ]
        allCurrencies = list(firstCurrenciesFromPairs +
                             secondCurrenciesFromPairs)
        uniqueCurrencies = []
        for currency in allCurrencies:
            if currency not in uniqueCurrencies:
                uniqueCurrencies.append(currency)
        return uniqueCurrencies


class Chart:

    def __init__(self, client, pair, timeFrame, startTime=None):
        timeFrames = {"day": 1440, "fourhour": 240,
                      "hour": 60, "halfhour": 30, "quater": 15}
        chosenTimeFrame = timeFrames[timeFrame]

        if startTime == None:
            timeDiff = timedelta(days=19)
            startTimeChart = str(
                datetime.today().date() - timeDiff) + " 00:00:00"
        else:
            startTimeChart = startTime

        endTimeChart = str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        # daty start/koniec w formacie timestamp
        startDateTimestampChart = int(datetime.timestamp(
            datetime.strptime(startTimeChart, '%Y-%m-%d %H:%M:%S')))*1000
        endDateTimestampChart = int(datetime.timestamp(
            datetime.strptime(endTimeChart, '%Y-%m-%d %H:%M:%S')))*1000

        chart_info = {
            "start": startDateTimestampChart,
            "end": endDateTimestampChart,
            "period": chosenTimeFrame,
            "symbol": pair,
            "ticks": 0
        }
        argument = {
            "info": chart_info
        }

        chartListDays = client.commandExecute(
            "getChartRangeRequest", argument)["returnData"]["rateInfos"]

        numberOfDigits = {"EURUSD": 5, "USDJPY": 3, "GBPUSD": 5, "AUDUSD": 5,
                          "USDCAD": 5, "EURJPY": 3, "EURGBP": 5, "GBPJPY": 3,
                          "AUDJPY": 3, "AUDCAD": 5, "CADJPY": 3}

        for note in chartListDays:
            note["high"] = (float(note["open"]) +
                            float(note["high"])) / 10 ** numberOfDigits[pair]
            note["low"] = (float(note["open"]) +
                           float(note["low"])) / 10 ** numberOfDigits[pair]
            note["close"] = (float(note["open"]) +
                             float(note["close"])) / 10 ** numberOfDigits[pair]
            note["open"] = float(note["open"]) / 10 ** numberOfDigits[pair]
        time.sleep(2)
        self.chartReady = chartListDays


class Resistance:

    def __init__(self, client, pair, timeFrame, startTime=None):
        daysOfWeek = {0: "mon", 1: "tue", 2: "wed",
                      3: "thu", 4: "fri", 5: "sat", 6: "sun"}
        chart = Chart(client, pair, timeFrame, startTime=startTime)

        if startTime == None:
            chartListDays = chart.chartReady
        else:
            chartListDays = chart.chartReady  # [:-1]

        for note in chartListDays:
            note["ctm"] = daysOfWeek[datetime.fromtimestamp(
                note["ctm"] / 1000).weekday()]

        amountOfWeeks = 3
        allCloses = []
        currentCloses = []
        mondayNo = 0
        if startTime == None:
            for note in list(reversed(chartListDays)):
                currentCloses.append(note["close"])
                if note["ctm"] == "sun":
                    mondayNo += 1
                    allCloses.append(max(currentCloses))
                    currentCloses = []
                if mondayNo == amountOfWeeks:
                    break
        else:
            for note in list(reversed(chartListDays)):
                currentCloses.append(note["close"])
            allCloses = max(currentCloses)

        self.allCloses = allCloses


class Support:

    def __init__(self, client, pair, timeFrame, startTime=None):
        daysOfWeek = {0: "mon", 1: "tue", 2: "wed",
                      3: "thu", 4: "fri", 5: "sat", 6: "sun"}
        chart = Chart(client, pair, timeFrame, startTime=startTime)

        if startTime == None:
            chartListDays = chart.chartReady
        else:
            chartListDays = chart.chartReady  # [:-1]

        for note in chartListDays:
            note["ctm"] = daysOfWeek[datetime.fromtimestamp(
                note["ctm"] / 1000).weekday()]
        amountOfWeeks = 3
        allCloses = []
        currentCloses = []
        mondayNo = 0

        if startTime == None:
            for note in list(reversed(chartListDays)):
                currentCloses.append(note["close"])
                if note["ctm"] == "sun":
                    mondayNo += 1
                    allCloses.append(min(currentCloses))
                    currentCloses = []
                if mondayNo == amountOfWeeks:
                    break
        else:
            for note in list(reversed(chartListDays)):
                currentCloses.append(note["close"])
            allCloses = min(currentCloses)

        self.allCloses = allCloses


class SlowStoch:

    def __init__(self, client, possibleTradesDict, timeFrame):

        possibleTradesSlowStoch = {}
        self.averageHighLowsPerPair = {}

        for pair in possibleTradesDict:
            chart = Chart(client, pair, timeFrame)
            time.sleep(1)
            chartListDays = chart.chartReady

            # slow stoch
            k = 25  # z ilu ma dni być liczony stoch
            ps = 15  # zwalnianie stocha
            currentK = 0

            lowsSlow = []
            highsSlow = []
            closeSlow = []
            pairsHighLows = []

            for note in list(reversed(chartListDays)):
                lowsSlow.append(note["low"])
                highsSlow.append(note["high"])
                closeSlow.append(note["close"])
                pairsHighLows.append(note["high"] - note["low"])
                currentK += 1
                if currentK == k + ps - 1:
                    break
            pairsHighLowsAverage = round(
                sum(pairsHighLows) / len(pairsHighLows), 5)
            self.averageHighLowsPerPair[pair] = pairsHighLowsAverage

            stochUp = []
            stochDown = []
            index = 0
            for index in range(ps):
                stochUp.append(
                    100 * (closeSlow[index] - min(lowsSlow[index: index + k])))
                stochDown.append(
                    max(highsSlow[index: index + k]) - min(lowsSlow[index: index + k]))

            possibleTradesSlowStoch[pair] = round(
                sum(stochUp) / sum(stochDown), 2)

        self.possibleTradesSlowStoch = possibleTradesSlowStoch

    @staticmethod
    def get_possible_trades_from_stoch(possibleCurrencyPairs, fourHourSS, oneHourSS, halfHourSS, quaterSS):
        possibleBuysStoch = {pair: "buy"
                             for pair in possibleCurrencyPairs
                             if quaterSS[pair] <= 20 and
                             fourHourSS[pair] < 50 and
                             (halfHourSS[pair] <= 20 or
                              oneHourSS[pair] <= 20)
                             }

        possibleSellsdStoch = {pair: "sell"
                               for pair in possibleCurrencyPairs
                               if quaterSS[pair] >= 80 and
                               fourHourSS[pair] > 50 and
                               (halfHourSS[pair] >= 80 or
                                oneHourSS[pair] >= 80)
                               }
        return {**possibleBuysStoch, **possibleSellsdStoch}


class MoneyManagement:

    def __init__(self, client):

        accountData = client.commandExecute("getMarginLevel")["returnData"]

        self.balance = accountData["balance"]
        self.margin = accountData["margin"]
        self.equity = accountData["equity"]
        self.marginFree = accountData["margin_free"]
        self.marginLevel = accountData["margin_level"]

    def count_volumes(self, resistanceDict, supportDict, possibleTradesWithAllOk):
        pass


class CurrentTrades:

    def __init__(self, client):

        tradesDirections = {0: "buy", 1: "sell"}

        arguments = {
            "openedOnly": True
        }

        self.openedTradesFullInfo = client.commandExecute(
            "getTrades", arguments)["returnData"]

        self.openedTradesOnlyPairs = {trade["symbol"]: tradesDirections[trade["cmd"]]
                                      for trade in self.openedTradesFullInfo
                                      }

        self.openedTradesOpeningPrices = {trade["symbol"]: trade["open_price"]
                                          for trade in self.openedTradesFullInfo
                                          }
        self.openedTradesVolumes = {trade["symbol"]: trade["volume"]
                                    for trade in self.openedTradesFullInfo
                                    }
        self.openedTradesOrdersNo = {trade["symbol"]: trade["order"]
                                     for trade in self.openedTradesFullInfo
                                     }
        self.openedTradesStopLoss = {trade["symbol"]: trade["sl"]
                                     for trade in self.openedTradesFullInfo
                                     }
        self.openedTradesTakeProfit = {trade["symbol"]: trade["tp"]
                                       for trade in self.openedTradesFullInfo
                                       }
        self.openedTradesResults = {trade["symbol"]: trade["profit"]
                                    for trade in self.openedTradesFullInfo
                                    }
        self.openedTradesOpenTimes = {trade["symbol"]: trade["open_time"]
                                      for trade in self.openedTradesFullInfo
                                      }

    def check_current_trades_get_unique(self, possibleTradesDict):
        currentTradesCurrencies = BullsAndBears.split_pairs_to_currencies(
            list(self.openedTradesOnlyPairs.keys()))

        toRemove = []
        for pair in possibleTradesDict:
            if pair[0:3] in currentTradesCurrencies or \
                    pair[3:6] in currentTradesCurrencies:
                toRemove.append(pair)

        possibleTradesDict = {pair: possibleTradesDict[pair]
                              for pair in possibleTradesDict
                              if pair not in toRemove
                              }
        return possibleTradesDict

    def close_trades(self, client, tradesToClose):

        for pair in tradesToClose:
            Trade(client, self.openedTradesOnlyPairs[pair], "close", pair,
                  self.openedTradesVolumes[pair], self.openedTradesOpeningPrices[pair],
                  order=self.openedTradesOrdersNo[pair])


class Trade:

    def __init__(self, client, buyORsell, openORclose, pair,
                 volume, price=1, stopLoss=None, takeProfit=None,
                 order=None):
        cmdField = {
            "buy": 0,
            "sell": 1,
            "buylimit": 2,
            "selllimit": 3,
            "buystop": 4,
            "sellstop": 5
        }

        typeField = {
            "open": 0,
            "close": 2,
            "modify": 3
        }

        tradeTransInfo = {
            "symbol": pair,
            "type": typeField[openORclose],
            "cmd": cmdField[buyORsell],
            "customComment": None,
            "expiration": None,
            "offset": 0,
            "order": order,
            "price": price,
            "sl": stopLoss,
            "tp": takeProfit,
            "volume": volume
        }

        arguments = {
            "tradeTransInfo": tradeTransInfo
        }

        self.response = client.commandExecute(
            "tradeTransaction", arguments)


class PositionParameters:

    def __init__(self, client, possibleTradesWithAllOk, currencyExchangePLN,
                 equity, resistanceDict, supportDict, possibleFullTradesCopy,
                 possibleSemiTradesCopy, highLows):

        self.positionTP = {}
        self.positionSL = {}
        self.positionLots = {}
        self.tradesToExecute = {}

        self.fullTrades = possibleFullTradesCopy
        self.semiTrades = possibleSemiTradesCopy
        self.allOkTrades = possibleTradesWithAllOk

        bidAsk = BidAsk(client, possibleTradesWithAllOk)

        timeDiff = timedelta(hours=0.5)
        startTimeChart = (
            datetime.now() - timeDiff).strftime("%Y-%m-%d %H:%M:%S")

        localSupportDict = {}
        localResistanceDict = {}
        for pair in possibleTradesWithAllOk:
            resistance = Resistance(
                client, pair, "quater", startTime=startTimeChart)
            time.sleep(1)
            support = Support(client, pair, "quater", startTime=startTimeChart)
            time.sleep(1)
            localResistanceDict[pair] = resistance.allCloses
            localSupportDict[pair] = support.allCloses

        for pair in possibleTradesWithAllOk:
            if pair in possibleFullTradesCopy:
                riskRate = 0.03
            elif pair in possibleSemiTradesCopy:
                riskRate = 0.02
            else:
                riskRate = 0.01

            if "JPY" in pair:
                decimals = 3
            else:
                decimals = 5

            if possibleTradesWithAllOk[pair] == "buy":

                if (resistanceDict[pair][0] - bidAsk.askPrices[pair]) / \
                    (bidAsk.askPrices[pair] -
                     round(bidAsk.bidPrices[pair] - highLows[pair] * 3, decimals)) >= 1.1:

                    if bidAsk.bidPrices[pair] > localResistanceDict[pair]:
                        self.positionSL[pair] = round(bidAsk.bidPrices[pair] -
                                                      highLows[pair] * 3, decimals)
                        self.positionTP[pair] = resistanceDict[pair][0]
                        self.tradesToExecute[pair] = possibleTradesWithAllOk[pair]
                        self.positionLots[pair] = truncate(equity / currencyExchangePLN[pair] /
                                                           100000 * riskRate /
                                                           (self.positionSL[pair] / bidAsk.askPrices[pair] -
                                                            1) * (-1), 2)

                elif (resistanceDict[pair][1] - bidAsk.askPrices[pair]) / \
                    (bidAsk.askPrices[pair] -
                     round(bidAsk.bidPrices[pair] - highLows[pair] * 3, decimals)) >= 1.1:

                    if bidAsk.bidPrices[pair] > localResistanceDict[pair]:
                        self.positionSL[pair] = round(bidAsk.bidPrices[pair] -
                                                      highLows[pair] * 3, decimals)
                        self.positionTP[pair] = resistanceDict[pair][1]
                        self.tradesToExecute[pair] = possibleTradesWithAllOk[pair]
                        self.positionLots[pair] = truncate(equity / currencyExchangePLN[pair] /
                                                           100000 * riskRate /
                                                           (self.positionSL[pair] / bidAsk.askPrices[pair] -
                                                            1) * (-1), 2)

            elif possibleTradesWithAllOk[pair] == "sell":

                if (bidAsk.bidPrices[pair] - supportDict[pair][0]) / \
                        (round(bidAsk.askPrices[pair] + highLows[pair] * 3, decimals) - bidAsk.bidPrices[pair]) >= 1.1:

                    if bidAsk.bidPrices[pair] > localSupportDict[pair]:
                        self.positionSL[pair] = round(bidAsk.askPrices[pair] +
                                                      highLows[pair] * 3, decimals)
                        self.positionTP[pair] = supportDict[pair][0] + \
                            bidAsk.spreads[pair]
                        self.tradesToExecute[pair] = possibleTradesWithAllOk[pair]
                        self.positionLots[pair] = round(equity / currencyExchangePLN[pair] /
                                                        100000 * riskRate /
                                                        (self.positionSL[pair] / bidAsk.bidPrices[pair] - 1), 2)

                elif (bidAsk.bidPrices[pair] - supportDict[pair][1]) / \
                        (round(bidAsk.askPrices[pair] + highLows[pair] * 3, decimals) - bidAsk.bidPrices[pair]) >= 1.1:
                    if bidAsk.bidPrices[pair] > localSupportDict[pair]:
                        self.positionSL[pair] = round(bidAsk.askPrices[pair] +
                                                      highLows[pair] * 3, decimals)
                        self.positionTP[pair] = supportDict[pair][1] + \
                            bidAsk.spreads[pair]
                        self.tradesToExecute[pair] = possibleTradesWithAllOk[pair]
                        self.positionLots[pair] = round(equity / currencyExchangePLN[pair] /
                                                        100000 * riskRate /
                                                        (self.positionSL[pair] / bidAsk.bidPrices[pair] - 1), 2)

    def execute_trades(self, client):

        for pair in self.tradesToExecute:
            Trade(client, self.tradesToExecute[pair], "open", pair,
                  self.positionLots[pair], stopLoss=self.positionSL[pair],
                  takeProfit=self.positionTP[pair])
            time.sleep(1)


class BidAsk:

    def __init__(self, client, pairList):

        self.bidPrices = {}
        self.askPrices = {}
        self.spreads = {}
        self.swapsLong = {}
        self.swapsShort = {}

        for pair in pairList:
            arguments = {
                "symbol": pair
            }
            response = client.commandExecute(
                "getSymbol", arguments)["returnData"]

            self.swapsLong[pair] = response["swapLong"]
            self.swapsShort[pair] = response["swapShort"]
            self.bidPrices[pair] = response["bid"]
            self.askPrices[pair] = response["ask"]
            self.spreads[pair] = round(response["ask"] - response["bid"], 5)
            time.sleep(1)


class Trends:

    def __init__(self, resistanceDict, supportDict, highLows):

        firstResistanceDirections = {pair: Trends.calculate_trend_progression(resistanceDict[pair][0],
                                                                              resistanceDict[pair][1],
                                                                              highLows[pair])
                                     for pair in resistanceDict
                                     }

        firstSupportDirections = {pair: Trends.calculate_trend_progression(supportDict[pair][0],
                                                                           supportDict[pair][1],
                                                                           highLows[pair])
                                  for pair in supportDict
                                  }

        secondResistanceDirections = {pair: Trends.calculate_trend_progression(resistanceDict[pair][0],
                                                                               resistanceDict[pair][2],
                                                                               highLows[pair])
                                      for pair in resistanceDict
                                      }

        secondSupportDirections = {pair: Trends.calculate_trend_progression(resistanceDict[pair][0],
                                                                            resistanceDict[pair][2],
                                                                            highLows[pair])
                                   for pair in supportDict
                                   }
        trendRanks = {pair: Trends.get_trend_rank(pair, firstResistanceDirections,
                                                  firstSupportDirections,
                                                  secondResistanceDirections,
                                                  secondSupportDirections)
                      for pair in supportDict
                      }

        self.trendsDict = {}
        for pair in trendRanks:
            if trendRanks[pair] > 1:
                self.trendsDict[pair] = "uptrend"
            elif trendRanks[pair] < 1:
                self.trendsDict[pair] = "downtrend"
            else:
                self.trendsDict[pair] = "side"

    @staticmethod
    def calculate_trend_progression(firstLevel, secondLevel, highLows):
        if firstLevel - secondLevel >= highLows and firstLevel - secondLevel > 0:
            return 1
        elif math.fabs(firstLevel - secondLevel) < highLows:
            return 0
        elif secondLevel - firstLevel >= highLows and secondLevel - firstLevel > 0:
            return -1

    @staticmethod
    def get_trend_rank(pair, firstDict, secondDict, thirdDict, fourthDict):

        rankList = []
        if pair in firstDict:
            rankList.append(firstDict[pair])
        if pair in secondDict:
            rankList.append(secondDict[pair])
        if pair in thirdDict:
            rankList.append(thirdDict[pair])
        if pair in fourthDict:
            rankList.append(fourthDict[pair])
        return sum(rankList)

    def check_trades_and_trends(self, tradesDict):

        tradesDirectionsInTrends = {
            "uptrend": "buy", "downtrend": "sell", "side": "both"}

        self.updatedPossibleTradesDict = {pair: tradesDirectionsInTrends[self.trendsDict[pair]]
                                          for pair in tradesDict
                                          if tradesDict[pair] == tradesDirectionsInTrends[self.trendsDict[pair]] or
                                          tradesDict[pair] == "both" or self.trendsDict[pair] == "side"
                                          }

        return self.updatedPossibleTradesDict


class FreeCurrencyConverter:

    @staticmethod
    def get_PLN_exchange_rate(possibleTradesWithAllOk):

        exchangeRatesJson = requests.get(
            "https://api.nbp.pl/api/exchangerates/tables/A?format=json").json()[0]["rates"]

        exchangeRates = {rate["code"]: rate["mid"]
                         for rate in exchangeRatesJson
                         }

        return {pair: exchangeRates[pair[0:3]]
                for pair in possibleTradesWithAllOk
                }


class TrailingStopLoss:

    def update_stop_loss(self, client, highLows, currentTrades):

        # {PARA: [OPEN, CLOSE, CLOSE, CLOSE, CLOSE], PARA2: [OPEN, CLOSE, CLOSE, CLOSE, CLOSE]}
        tradingCharts = {}
        onlyClosePricesList = []
        tradingPairsCurrentClosePrices = {}
        for pair in currentTrades.openedTradesOpenTimes:
            tradingCharts[pair] = Chart(
                client, pair, "quater",
                startTime=str(datetime.fromtimestamp(int(str(currentTrades.openedTradesOpenTimes[pair])[0:-3])))).chartReady

            onlyClosePricesList.append(
                currentTrades.openedTradesOpeningPrices[pair])

            for chartEntry in tradingCharts[pair]:
                onlyClosePricesList.append(chartEntry["close"])
            tradingPairsCurrentClosePrices[pair] = onlyClosePricesList
            onlyClosePricesList = []

        #  zwróć 1 jeśli close jest zgodny z trejdem, 0 jeśli jest reversal
        directionOfCharts = {}
        directionList = [1]
        for pair in tradingPairsCurrentClosePrices:
            previousClose = tradingPairsCurrentClosePrices[pair][0]
            index = 0
            amountOfCloses = len(tradingPairsCurrentClosePrices[pair]) - 1
            for _ in tradingPairsCurrentClosePrices[pair]:
                index += 1
                if index <= amountOfCloses:
                    if currentTrades.openedTradesOnlyPairs[pair] == "buy":
                        currentClose = tradingPairsCurrentClosePrices[pair][index]
                        if currentClose >= previousClose:
                            directionList.append(1)
                        else:
                            directionList.append(0)
                        previousClose = currentClose
                    elif currentTrades.openedTradesOnlyPairs[pair] == "sell":
                        currentClose = tradingPairsCurrentClosePrices[pair][index]
                        if currentClose <= previousClose:
                            directionList.append(1)
                        else:
                            directionList.append(0)
                        previousClose = currentClose
            directionOfCharts[pair] = directionList
            directionList = [1]
        print(directionOfCharts)
        print(tradingPairsCurrentClosePrices)
        # get last proper reversals

        lastReversals = TrailingStopLoss.get_last_proper_reversals(
            tradingPairsCurrentClosePrices, directionOfCharts, highLows, currentTrades)
        print(lastReversals)

        if len(list(lastReversals.keys())) > 0:
            # sprawdzenie czy porównanie następnego SL powinno być do OPEN czy do obecnego stop loss
            OpenStopLossReferenceForSpread = {}
            for pair in lastReversals:
                if currentTrades.openedTradesOnlyPairs[pair] == "buy":
                    if currentTrades.openedTradesStopLoss[pair] > currentTrades.openedTradesOpeningPrices[pair]:
                        OpenStopLossReferenceForSpread[pair] = currentTrades.openedTradesStopLoss[pair]
                    else:
                        OpenStopLossReferenceForSpread[pair] = currentTrades.openedTradesOpeningPrices[pair]

                elif currentTrades.openedTradesOnlyPairs[pair] == "sell":
                    if currentTrades.openedTradesStopLoss[pair] < currentTrades.openedTradesOpeningPrices[pair]:
                        OpenStopLossReferenceForSpread[pair] = currentTrades.openedTradesStopLoss[pair]
                    else:
                        OpenStopLossReferenceForSpread[pair] = currentTrades.openedTradesOpeningPrices[pair]

            # check if reversal is in profit
            lastReversalsUpdated = {}
            for pair in lastReversals:
                if currentTrades.openedTradesOnlyPairs[pair] == "buy":
                    if currentTrades.openedTradesOpeningPrices[pair] < lastReversals[pair]:
                        lastReversalsUpdated[pair] = lastReversals[pair]
                elif currentTrades.openedTradesOnlyPairs[pair] == "sell":
                    if currentTrades.openedTradesOpeningPrices[pair] > lastReversals[pair]:
                        lastReversalsUpdated[pair] = lastReversals[pair]
            lastReversals = lastReversalsUpdated
            print(lastReversals)

            # sprawdzenie czy polozenie reversal względem stoplossa/Opena zmieści spread i HighLows
            bidAsk = BidAsk(client, OpenStopLossReferenceForSpread)

            reversalsToUpdateStopLoss = {pair: lastReversals[pair]
                                         for pair in lastReversals
                                         if currentTrades.openedTradesResults[pair] > 0 and
                                         math.fabs(lastReversals[pair] - OpenStopLossReferenceForSpread[pair]) >
                                         bidAsk.spreads[pair] +
                                         highLows[pair] * 2
                                         }
            print(reversalsToUpdateStopLoss)

            # dodać wyjątek jeśli w danej parze 4 ostatnie close były na plus - w tym wypadku stop loss przesuwamy poniżej\
            # czwartego closea od końca (o ile będzie w strefie zarabiania)
            if len(tradingPairsCurrentClosePrices) >= 5:
                updateForReversals = {pair: tradingPairsCurrentClosePrices[pair][-5]
                                      for pair in currentTrades.openedTradesOnlyPairs
                                      if currentTrades.openedTradesResults[pair] > 0 and
                                      sum(directionOfCharts[pair][-5:-1]) == 4 and
                                      math.fabs(tradingPairsCurrentClosePrices[pair][-5] - OpenStopLossReferenceForSpread[pair]) >
                                      bidAsk.spreads[pair] +
                                      highLows[pair] * 2
                                      }
            else:
                updateForReversals = {}

            validReversalsToCalculateStopLoss = {
                **reversalsToUpdateStopLoss, **updateForReversals}
            print(validReversalsToCalculateStopLoss)

            # obliczenie nowych stoplossów
            stopLossValuesForUpdatingTrades = {}
            for pair in validReversalsToCalculateStopLoss:

                if currentTrades.openedTradesOnlyPairs[pair] == "buy":
                    stopLossValuesForUpdatingTrades[pair] = round(
                        validReversalsToCalculateStopLoss[pair] - highLows[pair] * 2, 5)

                elif currentTrades.openedTradesOnlyPairs[pair] == "sell":
                    stopLossValuesForUpdatingTrades[pair] = round(validReversalsToCalculateStopLoss[pair] +
                                                                  highLows[pair] * 2 + bidAsk.spreads[pair], 5)
            print(stopLossValuesForUpdatingTrades)

            for pair in stopLossValuesForUpdatingTrades:
                if "JPY" in pair:
                    stopLossValuesForUpdatingTrades[pair] = round(
                        stopLossValuesForUpdatingTrades[pair], 3)

            return stopLossValuesForUpdatingTrades

        else:
            return {}

    def execute_update_stoploss(self, client, currentTrades, stopLossToUpdate):
        for pair in stopLossToUpdate:

            print(Trade(client, currentTrades.openedTradesOnlyPairs[pair], "modify", pair,
                        currentTrades.openedTradesVolumes[pair], price=currentTrades.openedTradesOpeningPrices[pair],
                        stopLoss=stopLossToUpdate[pair], takeProfit=currentTrades.openedTradesTakeProfit[pair],
                        order=currentTrades.openedTradesOrdersNo[pair]))
            time.sleep(1)

    @staticmethod
    def get_last_chart_reversal_index(listName):
        indexes = [number
                   for number in range(len(listName))
                   if listName[number] == 0
                   ]
        return indexes[-1]

    @staticmethod
    def get_chart_reversals_indexes(listName):
        indexes = [number
                   for number in range(len(listName))
                   if listName[number] == 0
                   ]
        return indexes

    @staticmethod
    def get_last_proper_reversals(tradingPairsCurrentClosePrices, directionOfCharts, highLows, currentTrades):
        reversalsIndexes = {pair: TrailingStopLoss.get_chart_reversals_indexes(directionOfCharts[pair])
                            for pair in directionOfCharts
                            }
        lastReversals = {}

        for pair in directionOfCharts:
            for index in list(reversed(reversalsIndexes[pair])):
                if math.fabs(tradingPairsCurrentClosePrices[pair][-1] - tradingPairsCurrentClosePrices[pair][index]) >= highLows[pair]:

                    if currentTrades.openedTradesOnlyPairs[pair] == "buy":
                        if tradingPairsCurrentClosePrices[pair][-1] > tradingPairsCurrentClosePrices[pair][index]:
                            lastReversals[pair] = tradingPairsCurrentClosePrices[pair][index]

                    elif currentTrades.openedTradesOnlyPairs[pair] == "sell":
                        if tradingPairsCurrentClosePrices[pair][-1] < tradingPairsCurrentClosePrices[pair][index]:
                            lastReversals[pair] = tradingPairsCurrentClosePrices[pair][index]
                    break

        return lastReversals
