from tradingDefs import *
from tradingMail import SendMessage, gmailAddress
from copy import deepcopy
from datetime import datetime

import json
import eventlet
import socket
import logging
import time
import ssl
from threading import Thread
import os
import math
import stdiomask

daysOfWeek = {0: "mon", 1: "tue", 2: "wed",
              3: "thu", 4: "fri", 5: "sat", 6: "sun"}

userId = int(input("UserID: "))
password = stdiomask.getpass(prompt="Hasło: ", mask="*")


possibleCurrencyPairs = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD",
                         "USDCAD", "EURJPY", "EURGBP", "GBPJPY",
                         "AUDJPY", "AUDCAD", "CADJPY"]

importantCurrencies = ["EUR", "USD", "GBP", "AUD", "JPY", "CAD"]


message = []
fileName = 0
iteration = 0


while True:
    if message == []:
        pass
    else:
        SendMessage(gmailAddress, str(
            fileName) + ", Acc: " + str(userId), str(message))

    try:
        today = daysOfWeek[datetime.today().weekday()]
        if today == "sat" or today == "sun":
            print("czekam...")
            time.sleep(21600)
            continue
        else:
            # logger properties
            FORMAT = '[%(asctime)-15s][%(funcName)s:%(lineno)d] %(message)s'
            logging.basicConfig(format=FORMAT, level=logging.DEBUG,
                                filename='error_logs.log')

            client = APIClient()

            loginResponse = client.execute(
                loginCommand(userId=userId, password=password))

            if(loginResponse['status'] == False):
                print('Login failed. Error code: {0}'.format(
                    loginResponse['errorCode']))

            ssid = loginResponse['streamSessionId']

            endTime = datetime.now().strftime("%H")

            startCounter = time.perf_counter()

            today = daysOfWeek[datetime.today().weekday()]

            # sprawdzenie czy zmieniła się godzina - do LOGA
            startTime = datetime.now().strftime("%H")
            if startTime != endTime:
                hourChange = 1
            else:
                hourChange = 0

            if today == "sun" or today == "sun":
                # or today == "mon":
                break

            else:
                print()
                currentTrades = CurrentTrades(client)
                print("currentTrades", currentTrades.openedTradesOnlyPairs)
                transactionsToKill = []

                """ AWARYJNE SPRAWDZENIE CZY CENY NIE OMINĘŁY STOPLOSSÓW I TAKEPROFITÓW
                """

                # sprawdzenie czy ceny nie ominęły stoplossów i takeprofitów
                time.sleep(1)
                bidAsk = BidAsk(client, list(
                    currentTrades.openedTradesOnlyPairs.keys()))
                for pair in currentTrades.openedTradesOnlyPairs:
                    if currentTrades.openedTradesOnlyPairs[pair] == "buy":
                        if bidAsk.bidPrices[pair] < currentTrades.openedTradesStopLoss[pair] or\
                                bidAsk.bidPrices[pair] > currentTrades.openedTradesTakeProfit[pair]:
                            transactionsToKill.append(pair)
                    elif currentTrades.openedTradesOnlyPairs[pair] == "sell":
                        if bidAsk.askPrices[pair] > currentTrades.openedTradesStopLoss[pair]or\
                                bidAsk.askPrices[pair] < currentTrades.openedTradesTakeProfit[pair]:
                            transactionsToKill.append(pair)

                """ AUTOMATYCZNE ZAMKNIĘCIE TRANSAKCJI PRZED WEEKENDEM
                """

                # dodanie WSZYSTKICH transakcji do zamknięcia jeśli jest piątek 22:54
                today = daysOfWeek[datetime.today().weekday()]
                if today == "fri" and datetime.now().strftime("%H:%M:%S") >= "22:54:00":
                    transactionsToKill = list(
                        currentTrades.openedTradesOnlyPairs.keys())

                """ ZAMKNIĘCIE TRANSAKCJI Z LISTY transactionsToKill
                """
                print("To kill:", transactionsToKill)
                # zamknięcie transakcji, z listy transactionsToKill
                if len(transactionsToKill) > 0:
                    currentTrades.close_trades(client, transactionsToKill)

                """ AKTUALIZOWANIE STOPLOSSA DLA AKTUALNYCH TREJDÓW
                """

                slowStochQuater = SlowStoch(
                    client, possibleCurrencyPairs, "quater")
                print("slowStochQuater", slowStochQuater.possibleTradesSlowStoch)
                time.sleep(1)

                trailingStoploss = TrailingStopLoss()

                if len(list(currentTrades.openedTradesOnlyPairs.keys())) > 0:
                    # sprawdzenie czy można zaktualizować stop lossy i jeśli tak, obliczenie ich
                    stopLossesToUpdate = trailingStoploss.update_stop_loss(
                        client, slowStochQuater.averageHighLowsPerPair, currentTrades)

                    # zaktualizowanie stop lossów
                    if len(list(stopLossesToUpdate.keys())) > 0:
                        trailingStoploss.execute_update_stoploss(
                            client, currentTrades, stopLossesToUpdate)

                """  STWORZENIE LIST FULL I SEMI TREJDÓW Z DANYCH Z KALENDARZA
                """
                calendarFromApi = Calendar(client, importantCurrencies)
                bullsAndBears = BullsAndBears(calendarFromApi)
                print("Bull currencies:", bullsAndBears.bullCurrenciesUnique)
                print("Bear currencies:", bullsAndBears.bearCurrenciesUnique)

                # stworzenie listy możliwych pełnych (obie waluty mają przeciwne wydarzenia)
                # i semi trejdów (tylko jedna z walut ma wydarzenie)
                possibleFullTrades = bullsAndBears.get_possible_trades_both_lists(
                    possibleCurrencyPairs)

                possibleSemiTrades = bullsAndBears.get_semi_possible_trades(
                    possibleCurrencyPairs, importantCurrencies)

                possibleFullTradesCopy = deepcopy(possibleFullTrades)
                possibleSemiTradesCopy = deepcopy(possibleSemiTrades)
                print("possibleFullTradesCopy", possibleFullTradesCopy)
                print("possibleSemiTradesCopy", possibleSemiTradesCopy)

                """  STWORZENIE SŁOWNIKA ZE WSZYSTKIMI PARAMI WALUT I MOŻLIWYMI KIERUNKAMI NA PODSTAWIE KALENDARZA
                """
                possibleTradesFromCalendar = bullsAndBears.get_trade_directions_for_pairs(
                    possibleCurrencyPairs)
                print("possibleTradesFromCalendar", possibleTradesFromCalendar)

                """  WSPARCIA I OPORY
                """

                # obliczenie oporów i wsparć dla WSZYSTKICH możliwych trejdów
                supportDict = {}
                resistanceDict = {}
                for pair in possibleCurrencyPairs:
                    resistance = Resistance(client, pair, "fourhour")
                    time.sleep(1)
                    support = Support(client, pair, "fourhour")
                    time.sleep(1)
                    resistanceDict[pair] = resistance.allCloses
                    supportDict[pair] = support.allCloses

                print("supportDict", supportDict)
                print("resistanceDict", resistanceDict)

                """  WARTOŚCI SLOW STOCH
                """

                # obliczenie slow stoch dla możliwych trejdów
                slowStochFourHour = SlowStoch(
                    client, possibleCurrencyPairs, "fourhour")
                print("slowStochFourHour",
                      slowStochFourHour.possibleTradesSlowStoch)
                time.sleep(1)
                slowStochOneHour = SlowStoch(
                    client, possibleCurrencyPairs, "hour")
                print("slowStochOneHour", slowStochOneHour.possibleTradesSlowStoch)
                time.sleep(1)
                slowStochHalfHour = SlowStoch(
                    client, possibleCurrencyPairs, "halfhour")
                print("slowStochHalfHour",
                      slowStochHalfHour.possibleTradesSlowStoch)
                time.sleep(1)

                # możliwe sell i buy z slowstocha
                possibleTradesFromStoch = SlowStoch.get_possible_trades_from_stoch(possibleCurrencyPairs,
                                                                                   slowStochFourHour.possibleTradesSlowStoch,
                                                                                   slowStochOneHour.possibleTradesSlowStoch,
                                                                                   slowStochHalfHour.possibleTradesSlowStoch,
                                                                                   slowStochQuater.possibleTradesSlowStoch)
                print("possibleTradesFromStoch", possibleTradesFromStoch)
                """  TRENDY NA PODSTAWIE WSPARĆ I OPORÓW Z 3 TYG. I ŚR. HIGHLOWS
                """

                # określenie obecnych trendów
                trends = Trends(resistanceDict, supportDict,
                                slowStochFourHour.averageHighLowsPerPair)
                currentTrends = trends.trendsDict
                print("currentTrends", currentTrends)

                # możliwe trejdy z trendów KTÓRE SIĘ ZGADZAJĄ Z KALENDARZEM
                possibleTradesFromTrends = trends.check_trades_and_trends(
                    possibleTradesFromCalendar)
                print("possibleTradesFromTrends", possibleTradesFromTrends)

                """  MOŻLIWE TREJDY KORELUJĄCE Z TRENDÓW, KALENDARZA I STOCHA
                """

                possibleTradesWithOkTrendsAndStoch = {}
                for pairTrend in possibleTradesFromTrends:
                    for pairStoch in possibleTradesFromStoch:
                        if pairTrend == pairStoch and \
                                (possibleTradesFromTrends[pairTrend] == possibleTradesFromStoch[pairStoch] or
                                    possibleTradesFromTrends[pairTrend] == "both"):
                            possibleTradesWithOkTrendsAndStoch[pairTrend] = possibleTradesFromStoch[pairTrend]

                print("possibleTradesWithOkTrendsAndStoch",
                      possibleTradesWithOkTrendsAndStoch)

                """  USUNIĘCIE Z MOŻLIWYCH TREJDÓW PAR WALUT OBECNIE HANDLOWANYCH I POWTARZAJĄCYCH SIE
                """

                # ponowne pobranie obecnych transakcji
                time.sleep(1)
                currentTrades = CurrentTrades(client)

                # sprawdzenie możliwych trejdów z trendów i stocha z obecnymi trejdami i usunięcie ich
                withoutCurrent = currentTrades.check_current_trades_get_unique(
                    possibleTradesWithOkTrendsAndStoch)

                # przesunięcie FULL i SEMI trejdów na początek słownika
                shiftedOrderTrades = bullsAndBears.change_trades_order(
                    possibleSemiTradesCopy, possibleFullTradesCopy, withoutCurrent)
                print("shiftedOrderTrades", shiftedOrderTrades)

                # pozostawienie tylko tych trejdów, w których nie powtarzają się waluty ZOSTAJE TYLKO PIERWSZA PARA!
                possibleTradesWithAllOk = bullsAndBears.get_unique_currency_pairs(
                    shiftedOrderTrades)
                print("possibleTradesWithAllOk", possibleTradesWithAllOk)

                """OBLICZANIE POZYCJI DLA possibleTradesWithAllOk I OTWARCIE TREJDÓW
                """

                # przed obliczeniem pozycji najpierw trzeba pobrać kurs XX/PLN
                accountData = MoneyManagement(client)

                currencyExchangePLN = FreeCurrencyConverter.get_PLN_exchange_rate(
                    possibleTradesWithAllOk)

                print("currencyExchangePLN", currencyExchangePLN)

                # OBLICZANIE pozycji dla OkTradów
                positionsParameters = PositionParameters(client, possibleTradesWithAllOk, currencyExchangePLN,
                                                         accountData.equity, resistanceDict, supportDict,
                                                         possibleFullTradesCopy, possibleSemiTradesCopy,
                                                         slowStochQuater.averageHighLowsPerPair)

                # otwarcie OK trejdów
                positionsParameters.execute_trades(client)

                """ZAPISANIE LOGA
                """

                endTime = datetime.now().strftime("%H")
                iteration += 1

                if startTime != endTime or iteration % 8 == 0:
                    hourChange = 1
                else:
                    hourChange = 0
                hourChange = 1
                if hourChange == 1:
                    iteration = 0
                    endCounter = time.perf_counter()
                    loopTime = str(math.floor(
                        (endCounter - startCounter) / 60)) + ":" + str(round((endCounter - startCounter) % 60))

                    message = "loopTime: " + str(loopTime) + "\n" + \
                        "equity: " + str(accountData.equity) + "\n" + \
                        "obecne ceny BID: " + str(bidAsk.bidPrices) + "\n" + \
                        "obecne spready: " + str(bidAsk.spreads) + "\n" + \
                        "obecne trejdy: " + str(currentTrades.openedTradesOnlyPairs) + "\n" + \
                        "current results: " + str(currentTrades.openedTradesResults) + "\n" + \
                        "current trades openprices: " + str(currentTrades.openedTradesOpeningPrices) + "\n" + \
                        "current trades SL: " + str(currentTrades.openedTradesStopLoss) + "\n" + \
                        "current trades TP: " + str(currentTrades.openedTradesTakeProfit) + "\n \n" + \
                        "lista bull: " + str(bullsAndBears.bullCurrenciesUnique) + "\n" + \
                        "lista bear: " + str(bullsAndBears.bearCurrenciesUnique) + "\n" + \
                        "możliwe full trejdy: " + str(possibleFullTradesCopy) + "\n" + \
                        "możliwe semi trejdy: " + str(possibleSemiTradesCopy) + "\n" + \
                        "possibleTradesFromCalendar" + str(possibleTradesFromCalendar) + "\n \n" + \
                        "possibleTradesFromTrends: " + str(possibleTradesFromTrends) + "\n" + \
                        "possibleTradesFromStoch: " + str(possibleTradesFromStoch) + "\n" + \
                        "possibleTradesWithOkTrendsAndStoch: " + str(possibleTradesWithOkTrendsAndStoch) + "\n" + \
                        "possibleTradesWithAllOk: " + str(possibleTradesWithAllOk) + "\n \n" + \
                        "obecne trendy: " + str(currentTrends) + "\n \n" + \
                        "Slow stoch quater: " + str(slowStochQuater.possibleTradesSlowStoch) + "\n \n" + \
                        "Slow stoch halfhour: " + str(slowStochHalfHour.possibleTradesSlowStoch) + "\n \n" + \
                        "Slow stoch onehour: " + str(slowStochOneHour.possibleTradesSlowStoch) + "\n \n" + \
                        "Slow stoch fourhour: " + str(slowStochFourHour.possibleTradesSlowStoch) + "\n \n" + \
                        "Resistance: " + str(resistanceDict) + "\n \n" + \
                        "Support: " + str(supportDict)
                else:
                    message = []

                client.disconnect()
                continue

    except:
        logging.exception("UPS: ")
        message = "Przerwana pętla"
        time.sleep(2)
        continue
