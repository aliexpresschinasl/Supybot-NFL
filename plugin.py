# -*- coding: utf-8 -*-
###
# Copyright (c) 2012-2013, spline
# All rights reserved.
###

# my libs.
from BeautifulSoup import BeautifulSoup
import urllib2
import re
import collections
import string
from itertools import groupby, count
import datetime
import json
import sqlite3
import os.path
import unicodedata
from operator import itemgetter

# supybot libs
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
from supybot.i18n import PluginInternationalization, internationalizeDocstring
# python implementation of doublemetaphone
doublemetaphone = utils.python.universalImport('local.metaphone')

_ = PluginInternationalization('NFL')

@internationalizeDocstring
class NFL(callbacks.Plugin):
    """Add the help for "@plugin help NFL" here
    This should describe *how* to use this plugin."""
    threaded = True

    def __init__(self, irc):
        self.__parent = super(NFL, self)
        self.__parent.__init__(irc)
        self._nfldb = os.path.abspath(os.path.dirname(__file__)) + '/db/nfl.db'
        self._playersdb = os.path.abspath(os.path.dirname(__file__)) + '/db/nfl_players.db'

    ##############
    # FORMATTING #
    ##############

    def _red(self, string):
        """Returns a red string."""
        return ircutils.mircColor(string, 'red')

    def _yellow(self, string):
        """Returns a yellow string."""
        return ircutils.mircColor(string, 'yellow')

    def _green(self, string):
        """Returns a green string."""
        return ircutils.mircColor(string, 'green')

    def _bold(self, string):
        """Returns a bold string."""
        return ircutils.bold(string)

    def _blue(self, string):
        """Returns a blue string."""
        return ircutils.mircColor(string, 'blue')

    def _ul(self, string):
        """Returns an underline string."""
        return ircutils.underline(string)

    def _bu(self, string):
        """Returns a bold/underline string."""
        return ircutils.bold(ircutils.underline(string))

    ######################
    # INTERNAL FUNCTIONS #
    ######################

    def _splicegen(self, maxchars, stringlist):
        """Return a group of splices from a list based on the maxchars
        string-length boundary.
        """

        runningcount = 0
        tmpslice = []
        for i, item in enumerate(stringlist):
            runningcount += len(item)
            if runningcount <= int(maxchars):
                tmpslice.append(i)
            else:
                yield tmpslice
                tmpslice = [i]
                runningcount = len(item)
        yield(tmpslice)

    def _batch(self, iterable, size):
        """http://code.activestate.com/recipes/303279/#c7"""

        c = count()
        for k, g in groupby(iterable, lambda x:c.next()//size):
            yield g

    def _dtFormat(self, outfmt, instring, infmt):
        """Convert from one dateformat to another."""

        try:
            d = datetime.datetime.strptime(instring, infmt)
            output = d.strftime(outfmt)
        except:
            output = instring
        return output

    def _validate(self, date, format):
        """Return true or false for valid date based on format."""

        try:
            datetime.datetime.strptime(str(date), format) # format = "%m/%d/%Y"
            return True
        except ValueError:
            return False

    def _httpget(self, url, h=None, d=None):
        """General HTTP resource fetcher. Supports b64encoded urls."""

        if not url.startswith('http://'):
            url = self._b64decode(url)

        self.log.info(url)

        try:
            if h and d:
                page = utils.web.getUrl(url, headers=h, data=d)
            else:
                page = utils.web.getUrl(url)
            return page
        except utils.web.Error as e:
            self.log.error("I could not open {0} error: {1}".format(url,e))
            return None

    def _remove_accents(self, data):
        """Unicode normalize for news."""

        nkfd_form = unicodedata.normalize('NFKD', unicode(data))
        return u"".join([c for c in nkfd_form if not unicodedata.combining(c)])

    def _b64decode(self, string):
        """Returns base64 encoded string."""

        import base64
        return base64.b64decode(string)

    def _int_to_roman(self, i):
        """Returns a string containing the roman numeral from a number. For nflsuperbowl."""

        numeral_map = zip((1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1),
            ('M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I'))
        result = []
        for integer, numeral in numeral_map:
            count = int(i / integer)
            result.append(numeral * count)
            i -= integer * count
        return ''.join(result)

    def _millify(self, num):
        """Turns a number like 1,000,000 into 1M."""

        for unit in ['','k','M','B','T']:
            if num < 1000.0:
                return "%3.3f%s" % (num, unit)
            num /= 1000.0

    def _shortenUrl(self, url):
        """Shortens a long URL into a short one."""

        try:
            posturi = "https://www.googleapis.com/urlshortener/v1/url"
            data = json.dumps({'longUrl' : url})
            request = urllib2.Request(posturi, data, {'Content-Type':'application/json'})
            response = urllib2.urlopen(request)
            return json.loads(response.read())['id']
        except:
            return url

    ######################
    # DATABASE FUNCTIONS #
    ######################

    def _sanitizeName(self, name):
        """ Sanitize name. """

        name = name.lower()
        name = name.replace('.','')
        name = name.replace('-','')
        name = name.replace("'",'')
        # possibly strip jr/sr/III suffixes in here?
        return name

    def _similarPlayers(self, optname):
        """Return a dict containing the five most similar players based on optname."""

        optname = self._sanitizeName(optname)
        db = sqlite3.connect(self._playersdb)
        cursor = db.cursor()
        cursor.execute("select eid, rid, fullname from players")
        rows = cursor.fetchall()
        db.close()
        outlist = []

        for row in rows:
            tmpdict = {}
            tmpdict['dist'] = int(utils.str.distance(str(optname), str(row[2])))
            tmpdict['name'] = row[2]
            tmpdict['rid'] = row[1]
            tmpdict['eid'] = row[0]
            outlist.append(tmpdict)

        outlist = sorted(outlist, key=itemgetter('dist'), reverse=False)[0:5]
        return outlist

    def _playerLookup(self, table, optname):
        """Return the specific id in column (eid, rid) for player."""

        optname = self._sanitizeName(optname)
        db = sqlite3.connect(self._playersdb)
        cursor = db.cursor()
        query = "select %s from players WHERE eid in (select id from aliases WHERE name LIKE ? )" % (table)
        cursor.execute(query, ('%'+optname+'%',))
        aliasrow = cursor.fetchone()

        if aliasrow is None:
            cursor = db.cursor()
            query = "select %s from players WHERE fullname LIKE ?" % (table)
            cursor.execute(query, ('%'+optname.replace(' ', '%')+'%',))  # wrap in % and replace space with wc.
            row = cursor.fetchone()
            if row is None:  # we did not find a %name%match% nor alias.
                namesplit = optname.split()
                if len(namesplit) > 1:  # we have more than one, first and last. assume 0 is first, 1 is last.
                    fndm = doublemetaphone.dm(unicode(namesplit[0]))
                    lndm = doublemetaphone.dm(unicode(namesplit[1]))
                    if lndm[1] != '':  # if we have a secondary dm code.
                        query = "select %s FROM players WHERE lndm1='%s' AND lndm2='%s'" % (table, lndm[0], lndm[1])
                    else:
                        query = "select %s FROM players WHERE lndm1='%s'" % (table, lndm[0])
                    if fndm[1] != '': # likewise with first name.
                        query += " AND fndm1='%s' AND fndm2='%s'" % (fndm[0], fndm[1])
                    else:
                        query += " AND fndm1='%s'" % (fndm[0])
                else:  # assume one name given and that we check only on the last.
                    lndm = doublemetaphone.dm(unicode(namesplit[0]))
                    if lndm[1] != '':  # secondary dm code.
                        query = "SELECT %s FROM players WHERE lndm1='%s' AND lndm2='%s'" % (table, lndm[0], lndm[1])
                    else:
                        query = "SELECT %s FROM players WHERE lndm1='%s'" % (table, lndm[0])
                # now that we have DM query, execute.
                cursor.execute(query)
                row = cursor.fetchone()
                if row is None:  # dm failed. last chance to try. Using edit distance.
                    names = self._similarPlayers(optname)
                    if names[0]['dist'] < 7:  # edit distance less than seven, return.
                        optid = str(names[0][table])  # first one, less than seven, return eid.
                    else:  # after everything, we found nothing. Return 0
                        optid = "0"
                else: # dm worked so we return.
                    optid = str(row[0])
            else:  # fullname query worked so return.
                optid = str(row[0])
        else:  # return the id but from alias.
            optid = str(aliasrow[0])
        # close db and return the id.
        db.close()
        return optid

    def _validteams(self, conf=None, div=None):
        """Returns a list of valid teams for input verification."""

        conn = sqlite3.connect(self._nfldb)
        cursor = conn.cursor()

        if conf and not div:
            cursor.execute("select team from nfl where conf=?", (conf,))
        elif conf and div:
            query = "select team from nfl where conf='%s' AND div='%s'" % (conf,div)
            cursor.execute(query)
        else:
            cursor.execute("select team from nfl")

        teamlist = [str(item[0]) for item in cursor.fetchall()]
        cursor.close()
        return teamlist

    def _translateTeam(self, db, column, optteam):
        """Translates input team into specific team for application."""

        conn = sqlite3.connect(self._nfldb)
        cursor = conn.cursor()
        query = "select %s from nfl where %s='%s'" % (db, column, optteam)
        cursor.execute(query)
        row = cursor.fetchone()
        cursor.close()
        return (str(row[0]))

    def _eidlookup(self, eid):
        """Returns a playername for a specific EID."""

        conn = sqlite3.connect(self._playersdb)
        cursor = conn.cursor()
        cursor.execute("SELECT fullname FROM players WHERE eid=?", (eid,))
        row = cursor.fetchone()
        cursor.close()
        if row:
            return (str(row[0]))
        else:
            return None

    ###################
    # ALIAS FUNCTIONS #
    ###################

    def nflplayeraddalias(self, irc, msg, args, optid, optalias):
        """<eid> <alias>
        Add a player alias. Ex: 2330 gisele
        """

        optalias = optalias.lower()  # sanitize name so it conforms.

        db = sqlite3.connect(self._playersdb)
        cursor = db.cursor()
        try:
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.execute("INSERT INTO aliases VALUES (?, ?)", (optid, optalias,))
            db.commit()
            irc.reply("I have successfully added {0} as an alias to '{1} ({2})'.".format(optalias, self._eidlookup(optid), optid))
        except sqlite3.Error, e:
            # ERROR: I cannot insert alias: column name is not unique
            # ERROR: I cannot insert alias: foreign key constraint failed
            irc.reply("ERROR: I cannot insert alias: {0}".format(e)) #(e.args[0]))
        db.close()

    nflplayeraddalias = wrap(nflplayeraddalias, [('checkCapability', 'admin'), ('int'), ('text')])

    def nflplayerdelalias(self, irc, msg, args, optalias):
        """<player alias>
        Delete a player alias. Ex: gisele
        """

        optalias = optalias.lower()
        # conn.text_factory = str
        db = sqlite3.connect(self._playersdb)
        cursor = db.cursor()
        cursor.execute("SELECT id FROM aliases WHERE name=?", (optalias,))
        rowid = cursor.fetchone()
        if not rowid:
            irc.reply("ERROR: I do not have any aliases under '{0}'.".format(optalias))
            return
        else:
            cursor.execute("DELETE FROM aliases WHERE name=?", (optalias,))
            db.commit()
            irc.reply("I have successfully deleted the player alias '{0}' from '{1} ({2})'.".format(optalias, self._eidlookup(rowid[0]), rowid[0]))
        db.close()

    nflplayerdelalias = wrap(nflplayerdelalias, [('checkCapability', 'admin'), ('text')])

    def nflplayeralias(self, irc, msg, args, optplayer):
        """<player>
        Fetches aliases for player.
        """

        if optplayer.isdigit():
            pass
        else:
            lookupid = self._playerLookup('eid', optplayer)
        if lookupid == "0":
            irc.reply("ERROR: I did not find any NFL player in the DB matching: {0}".format(optplayer))
            return

        db = sqlite3.connect(self._playersdb)
        cursor = db.cursor()
        cursor.execute("SELECT name FROM aliases WHERE id=?", (lookupid,))
        rows = cursor.fetchall()

        if len(rows) > 0:
            output = ' | '.join([item[0] for item in rows])
        else:
            output = "None."

        irc.reply("{0}({1}) aliases: {2}".format(optplayer, lookupid, output))

    nflplayeralias = wrap(nflplayeralias, [('text')])

    ####################
    # PUBLIC FUNCTIONS #
    ####################

    def nflteams(self, irc, msg, args, optconf, optdiv):
        """<conf> <div>
        Display a list of NFL teams for input. Optional: use AFC or NFC for conference.
        Optionally, it can also display specific divisions with North, South, East or West. Ex: nflteams AFC East
        """

        if optconf and not optdiv:
            optconf = optconf.lower()
            if optconf == "afc" or optconf == "nfc":
                teams = self._validteams(conf=optconf)
            else:
                irc.reply("Conference must be AFC or NFC")
                return

        if optconf and optdiv:
            optconf = optconf.lower()
            optdiv = optdiv.lower()

            if optconf == "afc" or optconf == "nfc":
                if optdiv == "north" or optdiv == "south" or optdiv == "east" or optdiv == "west":
                    teams = self._validteams(conf=optconf, div=optdiv)
                else:
                    irc.reply("Division must be: North, South, East or West")
                    return
            else:
                irc.reply("Conference must be AFC or NFC")
                return

        if not optconf and not optdiv:
            teams = self._validteams()

        irc.reply("Valid teams are: {0}".format(" | ".join([ircutils.bold(item) for item in teams])))

    nflteams = wrap(nflteams, [optional('somethingWithoutSpaces'), optional('somethingWithoutSpaces')])

    def nflhof(self, irc, msg, args, optyear):
        """[year]
        Display NFL Hall Of Fame inductees for year. Defaults to the latest year.
        """

        if optyear:
            testdate = self._validate(optyear, '%Y')
            if not testdate or int(optyear) < 1963:  # superbowl era and on.
                irc.reply("ERROR: Invalid year. Must be YYYY and after 1966.")
                return

        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS9ob2Yv')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        table = soup.find('table', attrs={'id':'hofers'})
        rows = table.findAll('tr', attrs={'class':''})

        nflhof = collections.defaultdict(list)

        for row in rows:
            num = row.find('td', attrs={'align':'right'})
            if num:
                tds = row.findAll('td')
                player = tds[1].getText()
                pos = tds[2].getText()
                year = tds[3].getText()
                nflhof[int(year)].append("{0} ({1})".format(player, pos))

        if not optyear:  # if we don't have one specified, get the last year in the sort.
            optyear = nflhof.keys()[-1]

        output = nflhof.get(int(optyear), None)

        if not output:
            irc.reply("ERROR: Something broke looking up HOF class for: {0}".format(optyear))
            return
        else:
            irc.reply("{0} {1} :: {2}".format(self._bold(optyear), self._bold("NFL Hall of Fame class"), ' | '.join(output)))

    nflhof = wrap(nflhof, [optional('int')])

    def nflawards(self, irc, msg, args, optyear):
        """<year>
        Display NFL Awards for a specific year. Use a year from 1966 on. Ex: 2003
        """

        testdate = self._validate(optyear, '%Y')
        if not testdate or int(optyear) < 1966:  # superbowl era and on.
            irc.reply("ERROR: Invalid year. Must be YYYY and after 1966.")
            return

        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS95ZWFycy8=') + '%s/' % optyear # 1966 on.
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        if not soup.find('h2', text="Award Winners"):
            irc.reply("ERROR: Could not find NFL Awards for the %s season. Perhaps formatting changed or you are asking for the current season in-progress." % optyear)
            return

        table = soup.find('h2', text="Award Winners").findParent('div', attrs={'id':'awards'}).find('table')
        rows = table.findAll('tr')

        append_list = []

        for row in rows:
            award = row.find('td')
            player = award.findNext('td')
            append_list.append("{0}: {1}".format(self._bold(award.getText()), player.getText()))

        output = "{0} :: {1}".format(self._red(optyear + " NFL Awards"), " | ".join([item for item in append_list]))

        irc.reply(output)

    nflawards = wrap(nflawards, [('somethingWithoutSpaces')])

    def nflsuperbowl(self, irc, msg, args, optbowl):
        """<number>
        Display information from a specific Super Bowl. Ex: 39 or XXXIX
        """

        if optbowl.isdigit():
            try:
                optbowl = self._int_to_roman(int(optbowl))
            except:
                irc.reply("Failed to convert %s to a roman numeral" % optbowl)
                return

        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS9zdXBlci1ib3dsLw==')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        table = soup.find('table', attrs={'id':'superbowls'})
        rows = table.findAll('tr')[1:]

        sb_data = collections.defaultdict(list)

        for row in rows:
            tds = row.findAll('td')
            year = tds[0].getText()
            roman = tds[1].getText()
            roman = re.sub('[^A-Z_]+', '', roman, re.UNICODE)  # clean up roman here.
            t1 = tds[2].getText()
            t1score = tds[3].getText()
            t2 = tds[4].getText()
            t2score = tds[5].getText()
            mvp = tds[6].getText()
            loc = tds[7].getText()
            city = tds[8].getText()
            state = tds[9].getText()
            # value part is the appendString.
            appendString = "{0} Super Bowl {1} :: {2} {3} - {4} {5}  MVP: {6}  Location: {7} ({8}, {9})".format(\
                self._bold(year), self._red(roman), t1, t1score, t2, t2score, mvp, loc, city, state)
            # append now.
            sb_data[str(roman)] = appendString

        # output time.
        output = sb_data.get(str(optbowl), None)
        if output is None:
            irc.reply("ERROR: No Super Bowl found for: %s (Check formatting)" % optbowl)
        else:
            irc.reply(output)

    nflsuperbowl = wrap(nflsuperbowl, [('somethingWithoutSpaces')])

    def nflpracticereport (self, irc, msg, args, optteam):
        """<team>
        Display most recent practice report for team. Ex: NE.
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        url = self._b64decode('aHR0cDovL2hvc3RlZC5zdGF0cy5jb20vZmIvcHJhY3RpY2UuYXNw')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        timeStamp = soup.find('div', attrs={'id':'shsTimestamp'}).getText()
        tds = soup.findAll('td', attrs={'class':'shsRow0Col shsNamD', 'nowrap':'nowrap'})

        practicereport = collections.defaultdict(list)

        for td in tds:
            team = td.findPrevious('h2', attrs={'class':'shsTableTitle'})
            team = self._translateTeam('team', 'full', str(team.getText()))  # translate full team into abbr.
            player = td.find('a')
            appendString = "{0}".format(self._bold(player.getText()))
            report = td.findNext('td', attrs={'class':'shsRow0Col shsNamD'})
            if report:
                appendString += "({0})".format(report.getText())

            practicereport[team].append(appendString)

        output = practicereport.get(optteam, None)

        if output is None:
            irc.reply("No recent practice reports for: {0} as of {1}".format(self._red(optteam), timeStamp.replace('Last updated ','')))
        else:
            irc.reply("{0} Practice Report ({1}) :: {2}".format(self._red(optteam), timeStamp, " | ".join(output)))

    nflpracticereport = wrap(nflpracticereport, [('somethingWithoutSpaces')])

    def nflteamdraft(self, irc, msg, args, optteam, optyear):
        """<team> <year>
        Display a team's draft picks from a specific year.
        Ex: NE 2010
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("ERROR: Team not found. Must be one of: %s" % self._validteams())
            return
        # check team above. check year here.
        testdate = self._validate(str(optyear), '%Y')
        if not testdate:
            irc.reply("ERROR: Invalid year. Must be YYYY.")
            return
        if int(optyear) < 1965 or int(optyear) > datetime.datetime.now().year:
            irc.reply("ERROR: Year must be between 1965 and the current year.")
            return
        # build URL.
        url = self._b64decode('aHR0cDovL3d3dy5kcmFmdGhpc3RvcnkuY29tL2luZGV4LnBocC95ZWFycy8=') + '%s' % optyear
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html)
        table = soup.find('table', attrs={'border':'1'})  # this is amb.
        firstrow = table.find('tr')  # our simple error check.
        h1 = firstrow.find('h1')  # if draft is not available, like 2013 but in March, this will be None.
        if not h1:
            irc.reply("ERROR: Draft for %s is unavailable. Perhaps it has not occured yet?") % optyear
            return
        # if we do have h1, picks are from 3 and on due to header rows.
        rows = table.findAll('tr')[3:]
        # defaultdict(list) to put all picks in. key is the team. value = list of picks.
        teamdict = collections.defaultdict(list)
        # each row is a pick.
        for row in rows:
            tds = row.findAll('td')
            pick_no = tds[2].getText()
            pick_name = tds[3].getText()
            pick_team = tds[4].getText().lower()  # lower for translateTeam
            pick_pos = tds[5].getText()
            pick_col = tds[6].getText()
            # translate the team here.
            pick_team = self._translateTeam('team', 'dh', pick_team)
            appendString = "{0}. {1} ({2} {3})".format(pick_no, pick_name, pick_pos, pick_col)
            teamdict.setdefault(str(pick_team), []).append(appendString)

        # output time.
        output = teamdict.get(str(optteam))  # optteam = key
        if not output:
            irc.reply("ERROR: I did not find any picks for {0} in {1}. Perhaps something broke?".format(optteam, optyear))
            return
        else:
            irc.reply("{0} draft picks in {1}({2}):: {3}".format(self._red(optteam),\
                    self._bold(optyear), len(output), " | ".join(output)))

    nflteamdraft = wrap(nflteamdraft, [('somethingWithoutSpaces'), ('int')])

    def nflweather(self, irc, msg, args, optteam):
        """<team>
        Display weather for the next game. Ex: NE
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        url = self._b64decode('aHR0cDovL3d3dy5uZmx3ZWF0aGVyLmNvbS8=')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        table = soup.find('table', attrs={'class':'main'})
        if not table:
            irc.reply("Something broke in formatting with nflweather.")
            return

        tbody = table.find('tbody')
        rows = tbody.findAll('tr')

        weatherList = collections.defaultdict(list)

        for row in rows:  # grab all, parse, throw into a defaultdict for get method.
            tds = row.findAll('td')
            awayTeam = str(self._translateTeam('team', 'short', tds[0].getText()))  # translate into the team for each.
            homeTeam = str(self._translateTeam('team', 'short', tds[4].getText()))
            timeOrScore = tds[5].getText()
            gameTemp = tds[8].getText()

            appendString = "{0}@{1} - {2} - {3}".format(awayTeam, ircutils.bold(homeTeam), timeOrScore, gameTemp)
            weatherList[awayTeam].append(appendString)
            weatherList[homeTeam].append(appendString)

        output = weatherList.get(optteam, None)

        if output is None:
            irc.reply("No weather found for: %s. Team on bye?" % optteam)
        else:
            irc.reply(" ".join(output))

    nflweather = wrap(nflweather, [('somethingWithoutSpaces')])

    def nfltrans(self, irc, msg, args):
        """
        Display latest NFL transactions.
        """

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC90cmFuc2FjdGlvbnM=')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        div = soup.find('div', attrs={'id':'my-teams-table'})
        if not div:
            irc.reply("ERROR: Something went horribly wrong on formatting.")
            return
        table = div.find('table', attrs={'class':'tablehead'})
        rows = table.findAll('tr', attrs={'class':re.compile('^oddrow team.*|^evenrow team.*')})

        transactions = []

        for row in rows[0:6]:
            transdate = row.findPrevious('tr', attrs={'class':'stathead'}).getText()
            transdate = self._dtFormat("%m/%d", transdate, "%A, %B %d, %Y")
            tds = row.findAll('td')
            team = tds[0].find('a')['href']
            team = team.split('/', 7)[-1].split('/')[0].upper()  # splits up url nicely.
            news = utils.str.ellipsisify(tds[1].getText(), 150)
            # append to transactions.
            transactions.append("{0} :: {1} :: {2}".format(transdate, self._red(team), news))

        for transaction in transactions:
            irc.reply(transaction)

    nfltrans = wrap(nfltrans)

    def nflprobowl(self, irc, msg, args, optyear):
        """<year>
        Display NFL Pro Bowlers for a year. Ex: 2011.
        """

        # must test the date.
        testdate = self._validate(str(optyear), '%Y')
        if not testdate:
            irc.reply("Invalid year. Must be YYYY.")
            return
        if int(optyear) < 1950:
            irc.reply("Year must be 1950 or after.")
            return

        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS95ZWFycw==') + '/%s/probowl.htm' % optyear
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        # process html
        soup = BeautifulSoup(html)
        h1 = soup.find('h1')
        if not soup.find('table', attrs={'id':'pro_bowl'}):  # one last sanity check
            irc.reply("Something broke trying to read probowl data page. Did you try and check the current year before the roster is out?")
            return
        table = soup.find('table', attrs={'id':'pro_bowl'}).find('tbody')
        rows = table.findAll('tr', attrs={'class':''})

        # setup containers
        teams = {}
        positions = {}
        players = []

        # process each player.
        for row in rows:
            tds = row.findAll('td')
            pos = str(tds[0].getText())
            player = str(tds[1].getText())
            tm = str(tds[2].getText())
            teams[tm] = teams.get(tm, 0) + 1 # to count teams
            positions[pos] = positions.get(pos, 0) + 1 # to count positions
            players.append("{0}, {1} ({2})".format(self._bold(player), tm, pos)) # append player to list

        # we display the heading, total teams (len) and use teams, sorted in rev, top10.
        irc.reply("{0} :: Total Players: {1} - Total Teams: {2} - Top Teams: {3}".format(\
            self._red(h1.getText()), self._ul(len(players)), self._ul(len(teams)),\
                [k + ": " + str(v) for (k,v) in sorted(teams.items(), key=lambda x: x[1], reverse=True)[0:10]]))

        irc.reply("{0}".format(" | ".join(players)))

    nflprobowl = wrap(nflprobowl, [('int')])

    def nflfines(self, irc, msg, args, optlist):
        """[--num #]
        Display latest NFL fines. Use --num # to display more than 3. Ex: --num 5
        """

        # handle optlist/optnumber
        optnumber = '5'
        if optlist:
            for (key, value) in optlist:
                if key == 'num':  # between 1 and 10, go to 5
                    if value < 1 or value > 10:
                        optnumber = '5'
                    else:
                        optnumber = value

        url = self._b64decode('aHR0cDovL3d3dy5qdXN0ZmluZXMuY29t')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        # process html. little error checking.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES)
        heading = soup.find('div', attrs={'class':'title1'})
        div = soup.find('div', attrs={'class':'standing'})
        table = div.find('table')
        rows = table.findAll('tr', attrs={'class':'data'})

        append_list = []

        for row in rows[0:int(optnumber)]:
            tds = row.findAll('td')
            date = tds[0]
            # team = tds[2] # team is broken due to html comments
            player = tds[3]
            fine = tds[4]
            reason = tds[5]
            append_list.append("{0} {1} {2} :: {3}".format(date.getText(),\
                ircutils.bold(player.getText()), fine.getText(), reason.getText()))

        for i,each in enumerate(append_list[0:int(optnumber)]):
            if i is 0:  # only for header row.
                irc.reply("Latest {0} :: Total {1} Fines.".format(heading.getText(), len(rows)))
                irc.reply(each)
            else:
                irc.reply(each)

    nflfines = wrap(nflfines, [getopts({'num':('int')})])

    def nflweeklyleaders(self, irc, msg, args):
        """
        Display weekly NFL Leaders in various categories.
        """

        url = self._b64decode('aHR0cDovL20uZXNwbi5nby5jb20vbmZsL2xlYWRlcnM/d2pi')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html.replace('&nbsp;',''))
        tables = soup.findAll('table', attrs={'class':'table'})
        subheading = soup.find('div', attrs={'class':'sub dark'})

        weeklyleaders = collections.defaultdict(list)

        # parse each table, which is a stat category.
        for table in tables:
            rows = table.findAll('tr')  # all rows, first one, below, is the heading
            heading = rows[0].find('td', attrs={'class':'sec row', 'width':'65%'})
            append_list = []  # container per list
            for i,row in enumerate(rows[1:]):  # rest of the rows, who are leaders.
                tds = row.findAll('td')
                #rnk = tds[0]
                player = tds[1]
                stat = tds[2]  # +1 the count so it looks normal, bold player/team and append.
                append_list.append("{0}. {1} ({2})".format(i+1, ircutils.bold(player.getText()), stat.getText()))
            # one we have everything in the string, append, so we can move into the next category.
            weeklyleaders[str(heading.getText())] = append_list

        # output time.
        for i,x in weeklyleaders.items():
            irc.reply("{0} {1} :: {2}".format(self._red(i), self._red(subheading.getText()), " ".join(x)))

    nflweeklyleaders = wrap(nflweeklyleaders)

    def nfltopsalary(self, irc, msg, args, optlist, optposition):
        """[--average|--caphit] [position]
        Display various NFL player and team salary information.
        Use --average to display the highest average salary.
        Use --caphit to display highest cap-hit.
        Other option is: position. Use the command with an argument to display valid positions.
        """

        average, caphit = False, False
        for (option, arg) in optlist:
            if option == 'average':
                average, caphit = True, False
            if option == 'caphit':
                caphit, average = True, False

        positions = ['center','guard','tackle','tight-end','wide-receiver','fullback',\
            'running-back', 'quarterback', 'defensive-end', 'defensive-tackle', 'linebacker',\
             'cornerback', 'safety', 'kicker', 'punter', 'kick-returner', 'long-snapper']

        # construct url.
        url = self._b64decode('aHR0cDovL3d3dy5zcG90cmFjLmNvbS90b3Atc2FsYXJpZXM=') + '/nfl/'
        if average:
            url += 'average/'
        if caphit:
            url += 'cap-hit/'
        if optposition:
            if optposition not in positions:
                irc.reply("Position not found. Must be one of: %s" % positions)
                return
            else:
                url += '%s/' % optposition

        html = self._httpget(url, h={"Content-type": "application/x-www-form-urlencoded"}, d=utils.web.urlencode({'ajax':'1'}))
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        # process html.
        soup = BeautifulSoup(html.replace('\n',''))
        tbody = soup.find('tbody')
        rows = tbody.findAll('tr')[0:5] # just do top5 because some lists are long.

        append_list = []

        for row in rows:
            rank = row.find('td', attrs={'style':'width:20px;'}).find('center')
            #team = rank.findNext('td', attrs={'class':re.compile('logo.*?')}).find('img')['src'].replace('http://www.spotrac.com/assets/images/thumb/','').replace('.png','')
            # self._translateTeam('st', 'team', str(team))
            player = row.find('td', attrs={'class':re.compile('player .*?')}).find('a')
            position = player.findNext('span', attrs={'class':'position'})
            salary = row.find('span', attrs={'class':'playersalary'}).getText().replace('$','').replace(',','')
            append_list.append("{0}. {1} {2}".format(rank.getText().strip(), self._bold(player.getText().strip()), self._millify(float(salary))))

        title = self._red('NFL Top Salaries')
        # add to title, depending on what's going on
        if caphit:
            title += " (cap-hit)"
        if average:
            title += " (average salaries)"
        if optposition:
            title += " at %s" % (optposition)

        # now output
        irc.reply("{0}: {1}".format(title, " | ".join([item for item in append_list])))

    nfltopsalary = wrap(nfltopsalary, [(getopts({'average':'', 'caphit':''})), optional('somethingWithoutSpaces')])

    def nflleagueleaders(self, irc, msg, args, optlist, optcategory, optstat, optyear):
        """[--postseason|--num20] <category> <stat> [year]
        Display NFL statistical leaders in a specific category for a stat. Year, which can go back until 2001, is optional.
        Ex: Passing td or Punting punts 2003. Stats show regular season.
        Use --postseason to show post-season stats.
        Use --num20 prefix to show top20 instead of top10.
        """

        statsCategories = {
                'Passing': {
                    'qbr':'49',
                    'comp':'1',
                    'att':'2',
                    'comp%':'41',
                    'yards':'4',
                    'yards/gm':'42',
                    'td':'5',
                    'int':'3',
                    'sacked':'8',
                    'sackedyardslost':'9',
                    'fumbles':'47',
                    'fumbleslost':'48'
                },
                'Rushing': {
                    'rushes':'16',
                    'yards':'17',
                    'yards/g':'39',
                    'avg':'40',
                    'td':'18',
                    'fumbles':'47',
                    'fumbleslost':'48'
                },
                'Receiving': {
                    'receptions':'27',
                    'recyards':'28',
                    'yards/gm':'44',
                    'yards/avg':'45',
                    'longest':'30',
                    'yac':'46',
                    '1stdowns':'33',
                    'tds':'29',
                    'fumbles':'47',
                    'fumbleslost':'48'
                },
                'Kicking': {
                    '0-19':'208',
                    '20-29':'210',
                    '30-39':'212',
                    '40-49':'214',
                    '50+':'216',
                    'fgm':'222',
                    'fga':'221',
                    'pct':'230',
                    'longest':'224',
                    'xpm':'225',
                    'xpa':'226',
                    'xp%':'231'
                },
                'Returns':{
                    'kickoffreturns':'311',
                    'kickoffyards':'312',
                    'kickoffavg':'319',
                    'kickofflongest':'314',
                    'kickofftd':'315',
                    'puntreturns':'301',
                    'puntreturnyards':'302',
                    'puntreturnavg':'320',
                    'puntreturnlongest':'304',
                    'puntreturntds':'305'
                },
                'Punting': {
                    'punts':'402',
                    'puntyards':'403',
                    'puntavg':'411',
                    'puntlong':'408',
                    'puntwithin20':'404',
                    'puntwithin10':'405',
                    'faircatch':'401',
                    'touchback':'406',
                    'blocked':'407'
                },
                'Defense':{
                    'solotackles':'128',
                    'assistedtackles':'129',
                    'totaltackles':'130',
                    'sacks':'106',
                    'sacksyardslost':'107',
                    'stuffs':'101',
                    'stuffsyardslost':'102',
                    'int':'108',
                    'intyards':'109',
                    'inttds':'110',
                    'deftd':'103',
                    'forcedfumbles':'114',
                    'pd':'113',
                    'safety':'115'
                }
            }

        optcategory = optcategory.title()  # must title this category

        if optcategory not in statsCategories:
            irc.reply("Category must be one of: %s" % statsCategories.keys())
            return

        optstat = optstat.lower()  # stat key is lower. value is #.

        if optstat not in statsCategories[optcategory]:
            irc.reply("Stat for %s must be one of: %s" % (optcategory, statsCategories[optcategory].keys()))
            return

        if optyear:
            testdate = self._validate(optyear, '%Y')
            if not testdate:
                irc.reply("Invalid year. Must be YYYY.")
                return
            if int(optyear) < 2000:
                irc.reply("Year must be 2001 or after.")
                return

        postseason, outlimit = False, '10'
        for (option, arg) in optlist:
            if option == 'postseason':
                postseason = True
            if option == 'num20':
                outlimit = '20'

        url = self._b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbmZsL3N0YXRzL2J5Y2F0ZWdvcnk=')
        url += '?cat=%s&conference=NFL&sort=%s&timeframe=All' % (optcategory, statsCategories[optcategory][optstat])
        if optyear:  # don't need year for most current.
            if not postseason:
                url += '&year=season_%s' % optyear
            else:
                url += '&year=postseason_%s' % optyear

        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html.replace('&nbsp;',''))
        selectedyear = soup.find('select', attrs={'name':'year'}).find('option', attrs={'selected':'selected'})  # creative way to find the year.
        table = soup.find('tr', attrs={'class':'ysptblthmsts', 'align':'center'}).findParent('table')
        header = table.findAll('tr')[1].findAll('td')
        rows = table.findAll('tr')[2:]

        append_list = []

        for row in rows[0:int(outlimit)]:
            name = str(row.findAll('td')[0].getText())  # always first
            team = str(row.findAll('td')[1].getText())  # always next
            sortfield = row.find('span', attrs={'class':'yspscores'})  # whatever field you are sorting by will have this span inside the td.
            append_list.append("{0} ({1}) - {2}".format(self._bold(name), team, sortfield.getText()))

        title = "Top {0} in {1}({2}) for {3}".format(outlimit, optcategory, optstat, selectedyear.getText())
        output = "{0} :: {1}".format(self._red(title), " | ".join([item for item in append_list]))
        irc.reply(output)

    nflleagueleaders = wrap(nflleagueleaders, [(getopts({'postseason':'', 'num20':''})), ('somethingWithoutSpaces'), ('somethingWithoutSpaces'), optional('somethingWithoutSpaces')])

    def nflteamrankings(self, irc, msg, args, optteam):
        """<team>
        Display team rankings for off/def versus the rest of the NFL. Ex: NE
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC90ZWFtL18vbmFtZQ==') + '/%s/' % optteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        div = soup.find('div', attrs={'class':'mod-container mod-stat'})
        h3 = div.find('h3')
        statsfind = div.findAll('div', attrs={'class':re.compile('span-1.*?')})

        append_list = []

        for stats in statsfind:
            header = stats.find('h4')
            stat = stats.find('span', attrs={'class':'stat'})
            rank = stat.findNext('strong')
            append_list.append("{0} {1} ({2})".format(self._bold(header.getText()), stat.getText(), rank.getText()))

        descstring = " | ".join([item for item in append_list])
        irc.reply("{0} :: {1} :: {2}".format(self._red(optteam), self._ul(h3.getText()), descstring))

    nflteamrankings = wrap(nflteamrankings, [('somethingWithoutSpaces')])

    def nflweek(self, irc, msg, args, optlist, optweek):
        """[week #|next]
        Display this week's schedule in the NFL. Issue week # to display that week's games. Ex: 17.
        """

        usePre, useNext, outputWeek = False, False, False
        for (option, arg) in optlist:
            if option == 'pre':
                usePre = True

        if optweek:
            if optweek == "next":
                useNext = True
            elif optweek.isdigit():
                if usePre:
                    if 1 <= int(optweek) <= 4:
                       outputWeek = "Preseason Week %s" % optweek
                    else:
                        irc.reply("ERROR: Preseason week number must be between 1 and 4.")
                        return
                else:
                    if 1 <= int(optweek) <= 17:
                        outputWeek = "Week %s" % optweek
                    else:
                        irc.reply("ERROR: Week must be between 1-17")
                        return

        html = self._httpget('aHR0cDovL3MzLmFtYXpvbmF3cy5jb20vbmZsZ2MvYWxsU2NoZWR1bGUuanM=')
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        jsondata = json.loads(html)
        week = jsondata.get('week', None)  # work with the week data so we know where we are.
        if week is None:
            irc.reply("ERROR: Failed to load schedule.")
            return

        currentWeekName = week.get('current', {'current': None}).get('weekName', None)
        nextWeekName = week.get('next', {'next': None}).get('weekName', None)
        if currentWeekName is None:
            irc.reply("ERROR: Cannot figure out the current week.")
            return
        if useNext and not nextWeekName:
            irc.reply("ERROR: Cannot figure out the next week.")
            return

        games = jsondata.get('content', None)  # data in games.
        if games is None:
            irc.reply("ERROR: Failed to load the games data.")
            return

        if outputWeek:
            games = [item['games'] for item in games if item['weekName'] == outputWeek]
            weekOutput = outputWeek
        elif useNext:
            games = [item['games'] for item in games if item['weekName'] == nextWeekName]
            weekOutput = nextWeekName
        else:
            games = [item['games'] for item in games if item['weekName'] == currentWeekName]
            weekOutput = currentWeekName

        append_list = []

        for games in games:
            for t in games:
                awayTeam = self._translateTeam('team', 'nid', t['awayTeamId'])
                homeTeam = self._translateTeam('team', 'nid', t['homeTeamId'])
                append_list.append("[{0}] {1}@{2} {3}".format(t['date']['num'], awayTeam, homeTeam, t['date']['time']))

        output = "{0} :: {1}".format(self._bold(weekOutput), " | ".join([item for item in append_list]))
        irc.reply(output)

    nflweek = wrap(nflweek, [(getopts({'pre':''})), optional('somethingWithoutSpaces')])

    def nflstandings(self, irc, msg, args, optlist, optconf, optdiv):
        """[--detailed] [conf] [division]
        Display NFL standings for a division. Requires a conference and division.
        Use --detailed to display full table. Ex: AFC East
        """

        detailed = False
        for (option, arg) in optlist:
            if option == 'detailed':
                detailed = True

        optconf = optconf.upper()
        optdiv = optdiv.title()
        if optconf != "AFC" and optconf != "NFC":
            irc.reply("Conference must be AFC or NFC.")
            return
        if optdiv != "North" and optdiv != "South" and optdiv != "East" and optdiv != "West":
            irc.reply("Division must be North, South, East or West.")
            return

        if not detailed:
            url = self._b64decode('aHR0cDovL3MzLmFtYXpvbmF3cy5jb20vbmZsZ2MvZGl2X3N0YW5kaW5nczIuanM=')
        else:
            url = self._b64decode('aHR0cDovL3MzLmFtYXpvbmF3cy5jb20vbmZsZ2MvZGl2X3N0YW5kaW5ncy5qcw==')

        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        jsondata = json.loads(html)
        standings = jsondata.get('content', None)
        if standings is None:
            irc.reply("ERROR: Failed to load standings.")
            return

        # list comp what we need.
        teams = [item['teams'] for item in standings if item['conference'] == optconf and item['division'] == optdiv]

        # shorter-one liner standings. detailed below.
        if not detailed:
            append_list = []
            for item in teams:  # teams is a list of dicts
                for team in item:  # so we recurse
                    append_list.append("{0} {1} ({2})".format(self._translateTeam('team', 'nid', team['teamId']),\
                                                              team['winLossRecord'], team['percentage']))

            output = "{0} :: {1}".format(self._bu(optconf + " " + optdiv), " | ".join([item for item in append_list]))
            irc.reply(output)
        else:  # detailed.
            header = "{0:11} {1:>3} {2:>3} {3:>3} {4:<6} {5:<5} {6:<5} {7:<5} {8:<5} {9:<4} {10:<4} {11:<6} {12:<6}"\
            .format(self._ul(optconf + " " + optdiv),'W','L','T','PCT','HOME','ROAD','DIV','CONF','PF','PA','STRK','PDIFF')

            irc.reply(header)

            for item in teams:  # teams is a list of dicts
                for t in item:  # so we recurse
                    output = "{0:9} {1:3} {2:3} {3:3} {4:6} {5:5} {6:5} {7:5} {8:5} {9:4} {10:4} {11:6} {12:6}".format(t['team']['abbreviation'],\
                        t['wins'], t['losses'], t['ties'], t['percentage'], t['extra']['home_record'], t['extra']['road_record'],\
                        t['extra']['division_record'], t['extra']['conference_record'], t['extra']['points_for'], t['extra']['points_against'],\
                        t['extra']['home_record'], t['extra']['net_points'], t['extra']['last_5_record'])

                    irc.reply(output)

    nflstandings = wrap(nflstandings, [getopts({'detailed':''}), ('somethingWithoutSpaces'), ('somethingWithoutSpaces')])

    def _format_cap(self, figure):
        """Format cap numbers for nflcap command."""

        figure = figure.replace(',', '').strip()  # remove commas.
        if figure.startswith('-'):  # figure out if we're a negative number.
            negative = True
            figure = figure.replace('-','')
        else:
            negative = False

        try:  # try and millify.
            figure = self._millify(float(figure))
        except:
            figure = figure

        if negative:
            figure = "-" + figure
        # now return
        return figure

    def nflcap(self, irc, msg, args, optteam):
        """<team>
        Display team's NFL cap situation. Ex: GB
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        lookupteam = self._translateTeam('spotrac', 'team', optteam)

        url = self._b64decode('aHR0cDovL3d3dy5zcG90cmFjLmNvbS9uZmwv') + '%s/cap-hit/' % lookupteam
        html = self._httpget(url)  #, h={"Content-type": "application/x-www-form-urlencoded"}, d={'ajax':'1'})
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        teamtitle = soup.find('title')
        tbody = soup.find('tbody')

        capfigures = []

        captds = tbody.findAll('td', attrs={'class':'total team total-title'})
        for captd in captds:
            row = captd.findPrevious('tr')
            captitle = row.find('td', attrs={'class': 'total team total-title'})
            capfigure = row.find('td', attrs={'class': 'total figure'})
            capfigure = self._format_cap(capfigure.getText())
            capfigures.append("{0}: {1}".format(self._ul(captitle.getText()), capfigure))

        bottomrow = tbody.findAll('tr')
        bottomtds = bottomrow[-2].findAll('td')
        basesalary, signingbonus, otherbonus, totalcap = bottomtds[1].getText(), bottomtds[2].getText(), bottomtds[3].getText(), bottomtds[5].getText()
        capspace = bottomrow[-1].findAll('td')[-1].getText()  # last row, last td.

        descstring = " | ".join([item for item in capfigures])
        output = "{0} :: Base Salaries {1}  Signing Bonuses {2}  Other Bonus {3} :: {4} :: TOTAL CAP {5} :: SPACE {6}".format(\
            self._red(teamtitle.getText()), self._format_cap(basesalary), self._format_cap(signingbonus),\
                self._format_cap(otherbonus), descstring, self._format_cap(totalcap), self._bold(self._format_cap(capspace)))
        irc.reply(output)

    nflcap = wrap(nflcap, [('somethingWithoutSpaces')])

    def nflcoachingstaff(self, irc, msg, args, optteam):
        """<team>
        Display a NFL team's coaching staff. Ex: NE
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        url = self._b64decode('aHR0cDovL2VuLndpa2lwZWRpYS5vcmcvd2lraS9MaXN0X29mX2N1cnJlbnRfTmF0aW9uYWxfRm9vdGJhbGxfTGVhZ3VlX3N0YWZmcw==')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        tables = soup.findAll('table', attrs={'style':'text-align: left;'})

        coachingstaff = collections.defaultdict(list)

        for table in tables:
            listitems = table.findAll('li')[3:]
            for li in listitems:
                team = li.findPrevious('h3')
                team = self._translateTeam('team', 'full', team.getText())
                coachingstaff[str(team)].append(li.getText().replace(u' –',': '))

        output = coachingstaff.get(str(optteam), None)
        if not output:
            irc.reply("ERROR: Failed to find coaching staff for: {0}. Maybe something broke?".format(optteam))
        else:
            irc.reply("{0} :: {1}".format(self._red(optteam), " | ".join([item for item in output])))

    nflcoachingstaff = wrap(nflcoachingstaff, [('somethingWithoutSpaces')])

    def nfldepthchart(self, irc, msg, args, optteam, opttype):
        """<team> <offense|defense|special>
        Display team's depth chart for unit.
        Ex: NYJ offense
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        lookupteam = self._translateTeam('yahoo', 'team', optteam)

        opttype = opttype.lower()
        if opttype not in ('offense', 'defense', 'special'):
            irc.reply("ERROR: Type must be offense, defense or special.")
            return

        url = self._b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbmZsL3RlYW1z') + '/%s/depthchart?nfl-pos=%s' % (lookupteam, opttype)
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        if opttype == "offense":
            h4 = soup.find('h4', text="Offensive Depth Chart")
        elif opttype == "defense":
            h4 = soup.find('h4', text="Defensive Depth Chart")
        elif opttype == "special":
            h4 = soup.find('h4', text="Special Teams Depth Chart")
        else:
            irc.reply("ERROR: Something broke trying to find depthchart.")
            return

        table = h4.findNext('table').find('tbody')
        rows = table.findAll('tr')

        depthchart = []

        for row in rows:
            position = row.find('th', attrs={'class':'title'}).getText().strip()
            players = row.findAll('td', attrs={'class':'title'})
            depthchart.append("{0} :: {1}".format(self._ul(position), " | ".join([item.find('a').text for item in players])))

        for splice in self._splicegen('380', depthchart):
            irc.reply(" | ".join([depthchart[item] for item in splice]))

    nfldepthchart = wrap(nfldepthchart, [('somethingWithoutSpaces'), ('somethingWithoutSpaces')])

    def nflroster(self, irc, msg, args, optteam, optposition):
        """<team> <position/#>
        Display team roster by position group or person matching #.
        Position must be one of: QB, RB, WR, TE, OL, DL, LB, SD, ST
        Ex: nflroster NE QB (all QBs on NE) or NFL NE 12 (NE roster #12)
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        lookupteam = self._translateTeam('yahoo', 'team', optteam)

        useNum = True
        validpositions = {
                    'QB':'Quarterbacks',
                    'RB':'Running Backs',
                    'WR':'Wide Receivers/Tight Ends',
                    'TE':'Wide Receivers/Tight Ends',
                    'OL':'Offensive Line',
                    'DL':'Defensive Line',
                    'LB':'Linebackers',
                    'SD':'Secondary',
                    'ST':'Special Teams' }

        optposition = optposition.replace('#','') # remove # infront of # if there.
        if not optposition.isdigit(): # if we are not a digit, check if we're in valid positions.
            useNum = False
            if optposition not in validpositions:
                irc.reply("Error: When looking up position groups, it must be one of: %s" % validpositions.keys())
                return

        url = self._b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbmZsL3RlYW1z') + '/%s/roster' % lookupteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        # process html
        soup = BeautifulSoup(html)
        tbodys = soup.findAll('tbody')[1:]  #skip search header.

        # setup defaultdicts for output.
        nflroster = collections.defaultdict(list)
        positiongroups = collections.defaultdict(list)

        for tbody in tbodys:
            rows = tbody.findAll('tr')
            for row in rows:
                number = row.find('td')
                # playertype = row.findPrevious('h5')
                player = number.findNext('th', attrs={'class':'title'}).findNext('a')
                position = number.findNext('td')
                height = position.findNext('td')
                weight = height.findNext('td')
                age = weight.findNext('td')
                # exp = age.findNext('td')
                group = row.findPrevious('caption')
                nflroster[str(number.getText())].append("{0} ({1})".format(player.getText(), position.getText()))
                positiongroups[str(group.getText())].append("#{0} {1}".format(number.getText(), player.getText()))

        if useNum:
            if nflroster.has_key(str(optposition)):
                output = "{0} #{1} is: {2}".format(optteam, optposition, "".join(nflroster.get(str(optposition))))
            else:
                output = "I did not find a person matching number: {0} on {1}".format(optposition, optteam)
        else:
            output = "{0} on {1} :: {2}".format(optposition, optteam, " | ".join(positiongroups.get(str(validpositions[optposition]))))

        irc.reply("{0}".format(output))

    nflroster = wrap(nflroster, [('somethingWithoutSpaces'), ('somethingWithoutSpaces')])

    def nflteamdraftpicks(self, irc, msg, args, optteam):
        """<team>
        Display total NFL draft picks for a team and what round.
        """

        optteam = optteam.upper()

        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        url = self._b64decode('aHR0cDovL3d3dy5mZnRvb2xib3guY29tL25mbF9kcmFmdA==') + '/' + str(datetime.datetime.now().year) + '/nfl_draft_order_full.cfm'
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        if not soup.find('div', attrs={'id':'content_nosky'}):
            irc.reply("Something broke on formatting.")
            return

        div = soup.find('div', attrs={'id':'content_nosky'})
        h1 = div.find('h1', attrs={'class':'newpagetitle'}).getText()
        table = div.find('table', attrs={'class':'fulldraftorder'})
        rows = table.findAll('tr')[1:]  # skip the first row.

        nflteampicks = collections.defaultdict(list)

        for row in rows:
            tds = row.findAll('td')
            team = tds[0].getText().strip().replace('WAS','WSH')  # again a hack for people using WAS instead of WSH.
            numofpicks = tds[1].getText().strip()
            pickrounds = tds[2].getText().strip()
            appendString = "({0}) Picks: {1}".format(numofpicks, pickrounds)
            nflteampicks[str(team)].append(appendString)

        # get the team
        output = nflteampicks.get(optteam, None)

        # finally output
        if not output:
            irc.reply("Team not found. Something break?")
            return
        else:
            irc.reply("{0} :: {1} :: {2}".format(self._red(h1), self._bold(optteam), "".join(output)))

    nflteamdraftpicks = wrap(nflteamdraftpicks, [('somethingWithoutSpaces')])

    def nfldraftorder(self, irc, msg, args, optlist):
        """[--round #]
        Display current NFL Draft order for next year's draft.
        Will default to display the first round. Use --round # to display another (1-7)
        """

        optround = "1"  # by default, show round 1.

        # handle getopts.
        if optlist:
            for key, value in optlist:
                if key == 'round':
                    if value > 7 or value < 1:
                        irc.reply("ERROR: Round must be between 1-7")
                        return
                    else:
                        optround = value

        url = self._b64decode('aHR0cDovL3d3dy5mZnRvb2xib3guY29tL25mbF9kcmFmdA==') + '/' + str(datetime.datetime.now().year) + '/nfl_draft_order.cfm'
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        if not soup.find('div', attrs={'id':'content'}):
            irc.reply("Something broke in formatting on the NFL Draft order page.")
            return

        # now process html
        div = soup.find('div', attrs={'id':'content'})
        h1 = div.find('h1', attrs={'class':'newpagetitle'}).getText()
        optround = "Round %s" % (optround)  # create "optround" total hack but works.
        round = div.find('h2', text=optround).findNext('ol')  # ol container, found by text.
        rows = round.findAll('li')  # each li has an a w/the team.

        append_list = []

        # go through each and append to list. This is ugly but it works.
        for i, row in enumerate(rows):
            rowtext = row.find('a')
            if rowtext:
                rowtext.extract()
                rowtext = rowtext.getText().strip().replace('New York','NY')  # ugly spaces + wrong NY.
                rowtext = self._translateTeam('team', 'draft', rowtext)  # shorten teams.

            # now, handle appending differently depending on what's left in row after extract()
            if len(row.getText().strip()) > 0:  # handle if row has more after (for a trade)
                append_list.append("{0}. {1} {2}".format(i+1,rowtext, row.getText().strip()))  # +1 since it starts at 0.
            else:  # most of the time, it'll be empty.
                append_list.append("{0}. {1}".format(i+1,rowtext))

        # now output
        irc.reply("{0}({1}) :: {2}".format(self._red(h1), self._bold(optround), " ".join(append_list)))

    nfldraftorder = wrap(nfldraftorder, [getopts({'round': ('int')})])

    def nflplayoffs(self, irc, msg, args):
        """
        Display the current NFL playoff match-ups if the season ended today.
        """

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9zdGFuZGluZ3MvXy90eXBlL3BsYXlvZmZzL3NvcnQvY29uZmVyZW5jZVJhbmsvb3JkZXIvZmFsc2U=')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        if not soup.find('table', attrs={'class':'tablehead', 'cellpadding':'3'}):
            irc.reply("Failed to find table for parsing.")
            return

        table = soup.find('table', attrs={'class':'tablehead', 'cellpadding':'3'})
        rows = table.findAll('tr', attrs={'class': re.compile('^oddrow.*?|^evenrow.*?')})

        nflplayoffs = collections.defaultdict(list)

        for row in rows:  # now build the list. table has rows with the order. we work with 1-6 below when outputting.
            conf = row.findPrevious('tr', attrs={'class':'stathead'}).find('td', attrs={'colspan':'13'})
            conf = str(conf.getText().replace('National Football Conference','NFC').replace('American Football Conference','AFC'))

            tds = row.findAll('td')  # now get td in each row for making into the list
            rank = tds[0].getText()
            team = tds[1].getText().replace('z -', '').replace('y -', '').replace('x -', '').replace('* -','') # short.
            #self.log.info(str(team))
            #team = self._translateTeam('team', 'short', team)
            reason = tds[10].getText()
            appendString = "{0}".format(self._bold(team.strip()))
            nflplayoffs[conf].append(appendString)

        for i,x in nflplayoffs.iteritems():
            matchups = "{6} :: BYES: {4} and {5} | WC: {3} @ {0} & {2} @ {1} | In the Hunt: {7} & {8}".format(\
                x[2], x[3], x[4], x[5], x[0], x[1], self._red(i), x[6], x[7])
            irc.reply(matchups)

    nflplayoffs = wrap(nflplayoffs)


    def nflteamtrans(self, irc, msg, args, optteam):
        """<team>
        Shows recent NFL transactions for team. Ex: CHI
        """

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        lookupteam = self._translateTeam('eid', 'team', optteam)

        url = self._b64decode('aHR0cDovL20uZXNwbi5nby5jb20vbmZsL3RlYW10cmFuc2FjdGlvbnM=') + '?teamId=%s&wjb=' % lookupteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        t1 = soup.findAll('div', attrs={'class':re.compile('(^ind tL$|^ind alt$|^ind$)')})

        if len(t1) < 1:
            irc.reply("No transactions found for %s" % optteam)
            return

        for item in t1:
            if "href=" not in str(item):
                trans = item.findAll(text=True)
                irc.reply("{0:8} {1}".format(self._bold(trans[0]), trans[1]))

    nflteamtrans = wrap(nflteamtrans, [('somethingWithoutSpaces')])

    def nflinjury(self, irc, msg, args, optlist, optteam):
        """[--details] <TEAM>
        Show all injuries for team. Example: NYG or NE.
        Use --details to display full table with team injuries.
        """

        details = False
        for (option, arg) in optlist:
            if option == 'details':
                details = True

        optteam = optteam.upper()

        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        lookupteam = self._translateTeam('roto', 'team', optteam)

        url = self._b64decode('aHR0cDovL3d3dy5yb3Rvd29ybGQuY29tL3RlYW1zL2luanVyaWVzL25mbA==') + '/%s/' % lookupteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        if soup.find('div', attrs={'class': 'player'}):
            team = soup.find('div', attrs={'class': 'player'}).find('a').getText()
        else:
            irc.reply("No injuries found for: %s" % optteam)
            return

        table = soup.find('table', attrs={'align': 'center', 'width': '600px;'})
        t1 = table.findAll('tr')
        object_list = []
        for row in t1[1:]:
            td = row.findAll('td')
            d = collections.OrderedDict()
            d['name'] = td[0].find('a').text
            d['position'] = td[2].renderContents().strip()
            d['status'] = td[3].renderContents().strip()
            d['date'] = td[4].renderContents().strip().replace("&nbsp;", " ")
            d['injury'] = td[5].renderContents().strip()
            d['returns'] = td[6].renderContents().strip()
            object_list.append(d)

        if len(object_list) < 1:
            irc.reply("No injuries for: %s" % optteam)
            return

        if details:
            irc.reply("{0} - {1} total injuries".format(self._ul(team), len(object_list)))
            irc.reply("{0:25} {1:3} {2:15} {3:<7} {4:<15} {5:<10}".format("Name","POS","Status","Date","Injury","Returns"))

            for inj in object_list:
                output = "{0:27} {1:<3} {2:<15} {3:<7} {4:<15} {5:<10}".format(ircutils.bold( \
                    inj['name']),inj['position'],inj['status'],inj['date'],inj['injury'],inj['returns'])
                irc.reply(output)
        else:
            irc.reply("{0} - {1} total injuries".format(self._ul(team), len(object_list)))
            irc.reply(" | ".join([item['name'] + " (" + item['returns'] + ")" for item in object_list]))

    nflinjury = wrap(nflinjury, [getopts({'details':''}), ('somethingWithoutSpaces')])

    def nflvaluations(self, irc, msg, args):
        """
        Display current NFL team valuations from Forbes.
        """

        url = self._b64decode('aHR0cDovL3d3dy5mb3JiZXMuY29tL25mbC12YWx1YXRpb25zL2xpc3Qv')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        tbody = soup.find('tbody', attrs={'id':'listbody'})
        rows = tbody.findAll('tr')

        append_list = []

        for row in rows:
            tds = row.findAll('td')
            rank = tds[0].getText()
            team = tds[1].getText()
            value = tds[2].getText().replace(',','')  # value needs some mixing and to a float.
            append_list.append("{0}. {1} ({2})".format(rank, ircutils.bold(team), self._millify(float(value)*(1000000))))

        header = self._red("Current NFL Team Values")
        irc.reply("{0} :: {1}".format(header, " | ".join(append_list)))

    nflvaluations = wrap(nflvaluations)

    def nflpowerrankings(self, irc, msg, args, optteam):
        """[team]
        Display this week's NFL Power Rankings.
        Optional: use [team] to display specific commentary. Ex: ATL
        """

        if optteam:  # if we have a team, check if its valid.
            optteam = optteam.upper()
            if optteam not in self._validteams():
                irc.reply("Team not found. Must be one of: %s" % self._validteams())
                return

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wb3dlcnJhbmtpbmdz')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        # process HTML
        soup = BeautifulSoup(html)
        if not soup.find('table', attrs={'class':'tablehead'}):
            irc.reply("Something broke heavily formatting on powerrankings page.")
            return

        # go about regular html business.
        datehead = soup.find('div', attrs={'class':'date floatleft'})
        table = soup.find('table', attrs={'class':'tablehead'})
        headline = table.find('tr', attrs={'class':'stathead'})
        rows = table.findAll('tr', attrs={'class':re.compile('^oddrow|^evenrow')})

        powerrankings = []  # list to hold each one.
        prtable = {}

        for row in rows:  # one row per team.
            teamdict = {}  # teamdict to put into powerrankings list.
            tds = row.findAll('td')  # findall tds.
            rank = tds[0].getText()  # rank number.
            team = tds[1].find('div', attrs={'style':'padding:10px 0;'}).find('a').getText()  # finds short.
            shortteam = self._translateTeam('team', 'short', str(team))  # small abbreviation via the db.
            lastweek = tds[2].find('span', attrs={'class':'pr-last'}).getText().replace('Last Week:','').strip()  # rank #
            comment = tds[3].getText()  # comment.
            # check if we're up or down and insert a symbol.
            if int(rank) < int(lastweek):
                symbol = self._green('▲')
            elif int(rank) > int(lastweek):
                symbol = self._red('▼')
            else:  # - if the same.
                symbol = "-"

            # now add the rows to our data structures.
            powerrankings.append("{0}. {1} (prev: {2} {3})".format(rank,shortteam,symbol,lastweek))
            prtable[str(shortteam)] = "{0}. {1} (prev: {2} {3}) {4}".format(rank,team,symbol,lastweek,comment)

        # now output. conditional if we have the team or not.
        if not optteam:  # no team so output the list.
            irc.reply("{0} :: {1}".format(self._blue(headline.getText()), datehead.getText()))
            for N in self._batch(powerrankings, 12):  # iterate through each team. 12 per line
                irc.reply("{0}".format(string.join([item for item in N], " | ")))
        else:  # find the team and only output that team.
            output = prtable.get(str(optteam), None)
            if not output:
                irc.reply("I could not find: %s - Something must have gone wrong." % optteam)
                return
            else:
                irc.reply("{0} :: {1}".format(self._blue(headline.getText()), datehead.getText()))
                irc.reply("{0}".format(output))

    nflpowerrankings = wrap(nflpowerrankings, [optional('somethingWithoutSpaces')])

    def nflschedule(self, irc, msg, args, optlist, optteam):
        """<team>
        Display the last and next five upcoming games for team. Ex: NE
        """

        fullSchedule = False
        for (option, arg) in optlist:
            if option == 'full':
                fullSchedule = True

        optteam = optteam.upper()
        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        lookupteam = self._translateTeam('yahoo', 'team', optteam) # don't need a check for 0 here because we validate prior.

        if fullSchedule: # diff url/method.
            url = self._b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbmZsL3RlYW1z') + '/%s/schedule' % lookupteam
            html = self._httpget(url)
            if not html:
                irc.reply("ERROR: Failed to fetch {0}.".format(url))
                self.log.error("ERROR opening {0}".format(url))
                return

            soup = BeautifulSoup(html)
            table = soup.find('table', attrs={'summary':'Regular Season Games'})

            if not table:
                irc.reply("ERROR: Failed to find schedule for: %s") % optteam
                return

            tbody = table.find('tbody')
            rows = tbody.findAll('tr')

            append_list = []

            for row in rows:
                tds = row.findAll('td')
                week = tds[0]

                if row.find('td', attrs={'class':'title bye'}):
                    date = "BYE"
                    opp = ""
                    score = ""
                    appendString = "W{0}-{1}".format(ircutils.bold(week.getText()), ircutils.underline("BYE"))
                else:
                    date = tds[1].getText()
                    dateSplit = date.split(',', 1) # take the date, dump the rest.
                    date = dateSplit[1]
                    opp = tds[2] # with how the Tag/string comes in, we need to extract one part and format the other.
                    oppName = opp.find('span')
                    if oppName:
                        oppName.extract()
                    oppTeam = opp.find('a').getText()
                    #opp = tds[2].find('span').getText()
                    #opp = self._translateTeam('team','full', opp) # use the db to make a full team small.
                    score = tds[3].getText().replace('EDT','').replace('EST','').replace('pm','').replace('am','') # strip the garbage
                    #score = score.replace('W', ircutils.mircColor('W', 'green')).replace('L', ircutils.mircColor('L', 'red'))
                    appendString = "W{0}-{1} {2} {3}".format(ircutils.bold(week.getText()), date.strip(), oppTeam.strip(), score.strip())

                append_list.append(appendString)

            descstring = string.join([item for item in append_list], " | ")
            output = "{0} SCHED :: {1}".format(ircutils.mircColor(optteam, 'red'), descstring)
            irc.reply(output)
        else:
            url = self._b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbmZsL3RlYW1z') + '/%s/calendar/rss.xml' % lookupteam
            html = self._httpget(url)
            if not html:
                irc.reply("ERROR: Failed to fetch {0}.".format(url))
                self.log.error("ERROR opening {0}".format(url))
                return

            # clean this stuff up
            html = html.replace('<![CDATA[','').replace(']]>','').replace('EDT','').replace('\xc2\xa0',' ')

            soup = BeautifulSoup(html)
            items = soup.find('channel').findAll('item')

            append_list = []

            for item in items:
                title = item.find('title').renderContents().strip() # title is good.
                day, date = title.split(',')
                desc = item.find('description') # everything in desc but its messy.
                desctext = desc.findAll(text=True) # get all text, first, but its in a list.
                descappend = (''.join(desctext).strip()) # list transform into a string.
                if not descappend.startswith('@'): # if something is @, it's before, but vs. otherwise.
                    descappend = 'vs. ' + descappend
                descappend += " [" + date.strip() + "]"
                append_list.append(descappend) # put all into a list.

            descstring = " | ".join([item for item in append_list])
            output = "{0} {1}".format(self._bold(optteam), descstring)
            irc.reply(output)

    nflschedule = wrap(nflschedule, [(getopts({'full':''})), ('somethingWithoutSpaces')])

    def nflcountdown(self, irc, msg, args):
        """
        Display the time until the next NFL season starts.
        """

        dDelta = datetime.datetime(2013, 9, 05, 21, 30) - datetime.datetime.now()
        irc.reply("There are {0} days {1} hours {2} minutes {3} seconds until the start of the 2013 NFL Season.".format(\
                                            dDelta.days, dDelta.seconds/60/60, dDelta.seconds/60%60, dDelta.seconds%60))

    nflcountdown = wrap(nflcountdown)

    def nfldraft(self, irc, msg, args, optyear, optround):
        """[YYYY] [round #]
        Show the NFL draft round from year. Year must be 1996 or after and optional round must be between 1 and 7.
        Defaults to round 1 if round is not given. Ex: nfldraft 2000 6 (Would show the 6th round of the 2000 draft)
        """

        if optyear:
            testdate = self._validate(optyear, '%Y')
            if not testdate:
                irc.reply("Invalid year. Must be YYYY.")
                return
            if optyear < 1996:
                irc.reply("Year must be after 1996.")
                return
        if optround:
            if 1 <= optround <= 7:
                irc.reply("Draft round must be 1 or 7.")
                return

        # construct url. add parameters depending on opts above.
        url = self._b64decode('aHR0cDovL2luc2lkZXIuZXNwbi5nby5jb20vbmZsL2RyYWZ0L3JvdW5kcw==')
        if optyear:  # add year if we have it.
            url += '?year=%s' % (optyear)
        if optround:  # optional round.
            url += '&round=%s' % (optround)

        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        table = soup.find('table', attrs={'class':'tablehead draft-tracker'})
        if not table:
            irc.reply("error: could not find any draft information. Bad year or round?")
            return

        h2 = soup.find('h2')
        rows = table.findAll('tr', attrs={'class': re.compile('^oddrow.*?|^evenrow.*?')})

        object_list = []

        for row in rows:
            pickNumber = row.find('p', attrs={'class':'round-number'}).getText()
            pickName = row.find('p', attrs={'class':'player-name'})
            pickTeam = row.find('p', attrs={'class':'team-name'}).getText()
            if pickName:
                appendString = "{0}. {1} - {2}".format(self._bold(pickNumber), pickName.getText(), pickTeam)
            else:  # we won't have a pick leading up to the draft.
                appendString = "{0}. {1}".format(self._bold(pickNumber), pickTeam)

            if row.find('p', attrs={'class':'notes'}):
                appendString += " ({0})".format(row.find('p', attrs={'class':'notes'}).getText())

            object_list.append(appendString)

        # output header
        irc.reply("{0}: ".format(self._red(h2.getText().strip())))
        # output each round.
        for N in self._batch(object_list, 6):
            irc.reply(' | '.join(str(n) for n in N))

    nfldraft = wrap(nfldraft, [optional('somethingWithoutSpaces'), optional('somethingWithoutSpaces')])

    def nfltrades(self, irc, msg, args):
        """
        Display the last NFL 5 trades.
        """

        url = self._b64decode('aHR0cDovL3d3dy5zcG90cmFjLmNvbS9uZmwtdHJhZGUtdHJhY2tlci8=')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html
        soup = BeautifulSoup(html)
        table = soup.find('table', attrs={'border':'0'})
        tbodys = table.findAll('tbody')
        # list for output
        nfltrade_list = []
        # each tbody for days. lump it all together.
        for tbody in tbodys:
            rows = tbody.findAll('tr')
            for row in rows:
                player = row.find('td', attrs={'class':'player'}).find('a').getText()
                data = row.find('span', attrs={'class':'data'}).getText()
                date = row.findPrevious('th', attrs={'class':'tracker-date'}).getText()
                fromteam = row.findAll('td', attrs={'class':'playerend'})[0].find('img')['src'].split('/', 7)
                toteam = row.findAll('td', attrs={'class':'playerend'})[1].find('img')['src'].split('/', 7)
                # translate into TEAMS.
                fromteam = self._translateTeam('team','st', fromteam[6].replace('.png', ''))  # have to use silly
                toteam = self._translateTeam('team','st', toteam[6].replace('.png', ''))  # .png method with both.
                # create string. apppend.
                appendString = "{0} :: {1}{2}{3} :: {4} {5}".format(date, self._bold(fromteam), self._red('->'), self._bold(toteam), player, data)
                nfltrade_list.append(appendString)

        # output time.
        irc.reply("Last 5 NFL Trades")
        # now output the first 5.
        for each in nfltrade_list[0:5]:
            irc.reply(each)

    nfltrades = wrap(nfltrades)

    def nflarrests(self, irc, msg, args):
        """Display the last 6 NFL Arrests from NFL Nation."""

        url = self._b64decode('aHR0cDovL2FycmVzdG5hdGlvbi5jb20vY2F0ZWdvcnkvcHJvLWZvb3RiYWxsLw==')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        html = html.replace('&nbsp;', ' ').replace('&#8217;', '’')

        soup = BeautifulSoup(html)
        lastDate = soup.findAll('span', attrs={'class': 'time'})[0]
        divs = soup.findAll('div', attrs={'class': 'entry'})

        arrestlist = []

        for div in divs:
            title = div.find('h2').getText().encode('utf-8')
            datet = div.find('span', attrs={'class': 'time'}).getText().encode('utf-8')
            datet = self._dtFormat("%m/%d", datet, "%B %d, %Y")  # translate date.
            arrestedfor = div.find('strong', text=re.compile('Team:'))
            if arrestedfor:
                matches = re.search(r'<strong>Team:.*?</strong>(.*?)<br />', arrestedfor.findParent('p').renderContents(), re.I| re.S| re.M)
                if matches:
                    college = matches.group(1).replace('(NFL)','').encode('utf-8').strip()
                else:
                    college = "None"
            else:
                college = "None"
            arrestlist.append("{0} :: {1} - {2}".format(datet, title, college))

        # date math.
        a = datetime.date.today()
        b = datetime.datetime.strptime(str(lastDate.getText()), "%B %d, %Y")
        b = b.date()
        delta = b - a
        daysSince = abs(delta.days)

        # output
        irc.reply("{0} days since last NFL arrest".format(self._red(daysSince)))
        for each in arrestlist[0:6]:
            irc.reply(each)

    nflarrests = wrap(nflarrests)

    def nfltotalqbr(self, irc, msg, args, optlist):
        """[--postseason]
        Display the top10 NFL QBs, ranked by Total QBR. Use --postseason to display for postseason.
        """

        postseason = False
        for (option, arg) in optlist:
            if option == 'postseason':
                postseason = True

        if postseason:
            url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9xYnIvXy9zZWFzb250eXBlLzM=')
        else:
            url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9xYnI=')

        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)

        title = soup.find('div', attrs={'class': 'mod-header stathead'}).find('h4')
        table = soup.find('table', attrs={'class': 'tablehead'})
        rows = table.findAll('tr', attrs={'class': re.compile('^(odd|even)row.*')})[0:10]

        qbrlist = []

        for row in rows:
            rank = row.find('td', attrs={'align':'left'})
            name = rank.findNext('td').find('a')
            qbr = name.findNext('td', attrs={'class':'sortcell'})
            qbrlist.append("{0}. {1} {2}".format(rank.getText(), self._bold(name.getText()), qbr.getText()))

        output = " | ".join([item for item in qbrlist])
        irc.reply("{0}: {1}".format(self._red(title.text), output))

    nfltotalqbr = wrap(nfltotalqbr, [(getopts({'postseason':''}))])

    def nflcoach(self, irc, msg, args, optteam):
        """<team>
        Display the coach for team. Ex: NYJ
        """

        optteam = optteam.upper()

        if optteam not in self._validteams():
            irc.reply("Team not found. Must be one of: %s" % self._validteams())
            return

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9jb2FjaGVz')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html)
        if not soup.find('div', attrs={'id': 'my-players-table'}):
            irc.reply("Something broke parsing the formatting on {0}. Contact bot owner.".format(url))
            return
        div = soup.find('div', attrs={'id': 'my-players-table'})
        table = div.find('table', attrs={'class': 'tablehead'})
        rows = table.findAll('tr', attrs={'class': re.compile('(odd|even)row')})

        coachlist = collections.defaultdict(list)

        for row in rows:
            tds = row.findAll('td')
            coach = tds[0].getText().replace("  "," ")
            exp = tds[1].getText()
            team = tds[3].getText()
            team = self._translateTeam('team', 'full', team.strip())
            coachlist[str(team)] = "{0}({1})".format(coach, exp)

        output = coachlist.get(str(optteam), None)
        if not output:
            irc.reply("Something went horribly wrong looking up the coach for {0}.".format(optteam))
            return
        else:
            irc.reply("The NFL coach for {0} is {1}".format(self._red(optteam), output))

    nflcoach = wrap(nflcoach, [('somethingWithoutSpaces')])

    def nflnews(self, irc, msg, args):
        """
        Display the latest headlines from nfl.com
        """

        url = self._b64decode('aHR0cDovL3MzLmFtYXpvbmF3cy5jb20vbmZsZ2MvYWxsX25ld3NMaXN0Lmpz')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        try:
            jsondata = json.loads(html)['content']
        except:
            irc.reply("Failed to parse article json from: %s" % url)
            return

        for article in jsondata[0:6]:
            title = article.get('title', None)
            desc = article.get('description', None)
            link = article.get('linkURL', None)
            date = article.get('date_ago', None)

            if title and link:
                output = "{0} - {1}".format(self._bold(title), self._shortenUrl(link))
                irc.reply(output)

    nflnews = wrap(nflnews)

    #################################
    # NFL PLAYER DATABASE FUNCTIONS #
    #################################

    def nflplayers(self, irc, msg, args, optname):
        """<player>
        Search and find NFL players. Must enter exact/approx name since no fuzzy matching is done here.
        """

        optplayer = self._sanitizeName(optname)  # sanitize optname
        optplayer = optplayer.replace(' ','%')  # replace spaces with % to help query.
        db = sqlite3.connect(self._playersdb)
        cursor = db.cursor()
        cursor.execute("select eid, rid, fullname from players WHERE fullname LIKE ?", ('%'+optplayer+'%',))
        rows = cursor.fetchall()

        if len(rows) < 1:
            irc.reply("ERROR: Sorry, I did not find any players matching {0}".format(optname))
            return

        irc.reply("{0} | {1} | {2}".format("EID","RID","NAME"))
        for row in rows:
            irc.reply("{0} {1} {2}".format(row[0], row[1], row[2]))

    nflplayers = wrap(nflplayers, [('text')])

    def nflgame(self, irc, msg, args, optplayer):
        """<player>
        Display NFL player's game log for current/active game. Ex: Eli Manning
        """

        lookupid = self._playerLookup('eid', optplayer)
        if lookupid == "0":
            related = ' | '.join([item['name'].title() for item in self._similarPlayers(optplayer)])
            irc.reply("No player found for: '{0}'. Related names: {1}".format(optplayer, related))
            return

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvXy9pZA==') + '/%s/' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return

        if "No statistics available." in html:
            irc.reply("Sorry, no statistics found on the page for: %s" % optplayer.title())
            return

        soup = BeautifulSoup(html)

        currentGame, previousGame = True, True  # booleans for below.
        h4 = soup.find('h4', text="CURRENT GAME")
        if not h4:
            h4 = soup.find('h4', text="PREVIOUS GAME")
            if not h4:
                irc.reply("I could not find game statistics for: %s. Player not playing? Also try nflgamelog command." % optplayer.title())
                return
            else:
                previousGame = True
        else:
            currentGame = True

        div = h4.findParent('div').findParent('div')
        gameTime = False

        table = div.find('table', attrs={'class':'tablehead'})
        header = table.find('tr', attrs={'class':'colhead'}).findAll('th')[1:]
        row = table.findAll('tr')[1].findAll('td')[1:]

        output = string.join([ircutils.bold(each.getText()) + ": " + row[i].getText() for i,each in enumerate(header)], " | ")
        if gameTime:
            irc.reply("{0} :: {1} ({2} ({3}))".format(self._red(optplayer.title()), output, gameTime.getText(), gameTimeSpan.getText()))
        else:
            irc.reply("{0} :: {1}".format(self._red(optplayer.title()), output))

    nflgame = wrap(nflgame, [('text')])

    def nflplayernews(self, irc, msg, args, optplayer):
        """<player>
        Display latest news for NFL player. Ex: Tom Brady
        """
        useSPN = False  # simple bypass as I found wrold but am not sure how long it will work.
        if useSPN:  # conditional to use SPN here. We'll use rworld.
            lookupid = self._playerLookup('eid', optplayer)
            if lookupid == "0":
                related = ' | '.join([item['name'].title() for item in self._similarPlayers(optplayer)])
                irc.reply("No player found for: '{0}'. Related names: {1}".format(optplayer, related))
                return

            url = self._b64decode('aHR0cDovL20uZXNwbi5nby5jb20vbmZsL3BsYXllcnVwZGF0ZQ==') + '?playerId=%s&wjb=' % lookupid
            html = self._httpget(url)
            if not html:
                irc.reply("ERROR: Failed to fetch {0}.".format(url))
                self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
                return

            soup = BeautifulSoup(html)
            playerName = soup.find('div', attrs={'class': 'sub bold'})
            if not playerName:
                irc.reply("I could not find any news. Did formatting change?")
                return
            else:
                playerName = playerName.getText()

            if soup.find('div', attrs={'class': 'ind line'}):
                playerNews = soup.find('div', attrs={'class': 'ind line'})
                extraPlayerNews = playerNews.find('div', attrs={'style': 'font-style:italic;'})
                if extraPlayerNews:  # clean it up.
                    extraPlayerNews.extract()
                    playerNews = self._remove_accents(playerNews.getText())
                else:
                    playerNews = "No news found for player."
        else:
            lookupid = self._playerLookup('rid', optplayer)
            if lookupid == "0":
                related = ' | '.join([item['name'].title() for item in self._similarPlayers(optplayer)])
                irc.reply("No player found for: '{0}'. Related names: {1}".format(optplayer, related))
                return

            url = self._b64decode('aHR0cDovL2Rldi5yb3Rvd29ybGQuY29tL3NlcnZpY2VzL21vYmlsZS5hc214L0dldEpTT05TaW5nbGVQbGF5ZXJOZXdzP3Nwb3J0PU5GTA==') + '&playerid=%s' % lookupid
            html = self._httpget(url)
            if not html:
                irc.reply("ERROR: Failed to fetch {0}.".format(url))
                self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
                return

            jsondata = json.loads(html)

            if len(jsondata) < 1:
                playerNews = "I did not find any news for player."
            else:
                jsondata = jsondata[0]
                playerName = jsondata['FirstName'] + " " + jsondata['LastName']
                timestamp = jsondata.get('TimeStamp', None) # RawTimeStamp
                headline = jsondata.get('Headline', None)
                impact = jsondata.get('Impact', None)
                news = jsondata.get('News', None)
                # now construct playernews
                playerNews = ""
                if timestamp: playerNews += "{0}".format(timestamp)
                if headline: playerNews += " {0}".format(self._remove_accents(headline))
                if news: playerNews += " {0}".format(self._remove_accents(news))
                if impact: playerNews += " {0}".format(self._remove_accents(impact))

        # finally, lets output.
        output = "{0} :: {1}".format(self._red(playerName), utils.str.normalizeWhitespace(playerNews))
        irc.reply(output)

    nflplayernews = wrap(nflplayernews, [('text')])

    def nflinfo(self, irc, msg, args, optplayer):
        """<player>
        Display basic information on NFL player. Ex: Tom Brady
        """

        lookupid = self._playerLookup('eid', optplayer)
        if lookupid == "0":
            related = ' | '.join([item['name'].title() for item in self._similarPlayers(optplayer)])
            irc.reply("No player found for: '{0}'. Related names: {1}".format(optplayer, related))
            return

        url = self._b64decode('aHR0cDovL20uZXNwbi5nby5jb20vbmZsL3BsYXllcmluZm8=') + '?playerId=%s&wjb=' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return

        soup = BeautifulSoup(html)
        team = soup.find('td', attrs={'class': 'teamHeader'}).find('b')
        playerName = soup.find('div', attrs={'class': 'sub bold'})
        divs = soup.findAll('div', attrs={'class': re.compile('^ind tL$|^ind alt$|^ind$')})

        append_list = []

        for div in divs:
            bold = div.find('b')
            if bold:
                key = bold
                bold.extract()
                value = div
                append_list.append("{0}: {1}".format(self._ul(key.getText()), value.getText()))

        descstring = " | ".join([item for item in append_list])
        output = "{0} :: {1} :: {2}".format(self._red(playerName.getText()),self._bold(team.getText()), descstring)

        irc.reply(output)

    nflinfo = wrap(nflinfo, [('text')])

    def nflcontract(self, irc, msg, args, optplayer):
        """<player>
        Display NFL contract for Player Name. Ex: Ray Lewis
        """

        lookupid = self._playerLookup('rid', optplayer)
        if lookupid == "0":
            related = ' | '.join([item['name'].title() for item in self._similarPlayers(optplayer)])
            irc.reply("No player found for: '{0}'. Related names: {1}".format(optplayer, related))
            return
        elif not lookupid.isdigit():
            irc.reply("ERROR: no RID found in DB for: {0}".format(optplayer))
            return

        url = self._b64decode('aHR0cDovL3d3dy5yb3Rvd29ybGQuY29tL3BsYXllci9uZmwv') + '%s/' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return

        soup = BeautifulSoup(html)
        pn = soup.find('div', attrs={'class':'playercard', 'style':'display:none;', 'id': re.compile('^cont_.*')})

        if not pn:
            irc.reply("ERROR: No contract found for: %s" % optplayer)
            return

        h1 = soup.find('h1').getText().split('|',1)[0].strip()
        p1 = pn.find('div', attrs={'class': 'report'}).getText()
        contract = re.sub('<[^<]+?>', '', p1).strip()
        contract = utils.str.normalizeWhitespace(contract)  # kill double spacing.

        irc.reply("{0} :: {1}".format(self._red(h1), contract))

    nflcontract = wrap(nflcontract, [('text')])

    def nflcareerstats(self, irc, msg, args, optplayer):
        """<player>
        Look up NFL career stats for a player. Ex: nflcareerstats tom brady
        """

        lookupid = self._playerLookup('eid', optplayer)
        if lookupid == "0":
            related = ' | '.join([item['name'].title() for item in self._similarPlayers(optplayer)])
            irc.reply("No player found for: '{0}'. Related names: {1}".format(optplayer, related))
            return

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvc3RhdHMvXy9pZA==') + '/%s/' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return

        if "No stats available." in html:
            irc.reply("No stats available for: %s" % optplayer)
            return

        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES)
        if not soup.find('a', attrs={'class': 'btn-split-btn'}): # check if player is active.
            irc.reply("Cannot find any career stats for an inactive/unsigned player: %s" % optplayer)
            return
        # experience.
        exp = soup.find('span', text="Experience")
        if exp:
            exp = exp.findParent('li')
            exp.span.extract()
        # position
        pos = soup.find('ul', attrs={'class': 'general-info'}).find('li', attrs={'class': 'first'}).getText().upper()
        pos = ''.join([eachLetter for eachLetter in pos if eachLetter.isalpha()])
        # basics.
        playername = soup.find('a', attrs={'class': 'btn-split-btn'}).getText().strip()
        article = soup.find('div', attrs={'class': 'article'})
        divs = article.findAll('table', attrs={'class': 'tablehead'})  # each one.

        # what to look for with each position
        postostats = {
            'QB': ['passing', 'rushing'],
            'RB': ['rushing', 'receiving'],
            'FB': ['rushing', 'receiving'],
            'WR': ['receiving', 'rushing'],
            'TE': ['receiving', 'rushing'],
            'DE': ['defensive'],
            'DT': ['defensive'],
            'LB': ['defensive'],
            'CB': ['defensive'],
            'S': ['defensive'],
            'PK': ['kicking'],
            'P': ['punting']
        }

        # prepare dicts for output
        stats = {}  # holds the actual stats
        statcategories = {}  # holds the categories.

        # expanded careerstats.
        for f, div in enumerate(divs):
            if div.find('tr', attrs={'class': 'colhead'}):
                if not div.find('tr', attrs={'class': 'total'}, text="There are no stats available."):
                    stathead = div.find('tr', attrs={'class': 'stathead'})
                    colhead = div.find('tr', attrs={'class': 'colhead'}).findAll('td')[1:]
                    totals = div.find('tr', attrs={'class': 'total'}).findAll('td')[1:]
                    tmplist = []
                    for i, total in enumerate(totals):
                        tmplist.append(ircutils.bold(colhead[i+1].getText()) + ": " + total.getText())
                    stats[int(f)] = tmplist
                    statcategories[str(stathead.getText().replace('Stats', '').strip().lower())] = f

        # now output.
        output = []
        if postostats.has_key(pos):  # if we want specific stats.
            for each in postostats[pos]:
                if statcategories.has_key(each):
                    output.append("{0}: {1}".format(ircutils.underline(each.title()), " | ".join(stats.get(statcategories[each]))))
        else:
            output.append("No stats for the {0} position.".format(pos))

        if exp:
            irc.reply("{0}({1} exp) career stats :: {2}".format(self._red(playername), exp.getText()," || ".join(output)))
        else:
            irc.reply("{0} career stats :: {1}".format(self._red(playername), " || ".join(output)))

    nflcareerstats = wrap(nflcareerstats, [('text')])

    def nflseason(self, irc, msg, args, optlist, optplayer):
        """[--year DDDD] <player>
        Look up NFL Season stats for a player. Ex: nflseason tom brady.
        To look up a different year, use --year YYYY. Ex: nflseason --year 2010 tom brady
        """

        season = False
        if optlist:
            for (key,value) in optlist:
                if key == 'year': # check our year. validate below.
                    season = self._validate(str(value), '%Y')
                    if not season:
                        irc.reply("%s is an invalid year. Must be YYYY." % value)
                        return
                    else:
                        season = str(value)

        if not season:
            # Season stats do not appear until after the first week of games, which is always going to be first weekend in September
            # So, we account for this using September 9 of each year as the time to use the current year, otherwise, subtract 1 year.
            if datetime.datetime.now().month < 9:
                season = str(datetime.datetime.now().year - 1)
            elif datetime.datetime.now().month == "9" and datetime.datetime.now().day < 9:
                season = str(datetime.datetime.now().year - 1)
            else:
                season = str(datetime.datetime.now().year)

        # now, handle the rest.
        lookupid = self._playerLookup('eid', optplayer)
        if lookupid == "0":
            related = ' | '.join([item['name'].title() for item in self._similarPlayers(optplayer)])
            irc.reply("No player found for: '{0}'. Related names: {1}".format(optplayer, related))
            return

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvc3RhdHMvXy9pZA==') + '/%s/' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return

        if "No stats available." in html:
            irc.reply("No stats available for: %s" % optplayer)
            return

        soup = BeautifulSoup(html)

        if not soup.find('a', attrs={'class':'btn-split-btn'}):  # check if player is active.
            irc.reply("Cannot find any season stats for an inactive/unsigned player: %s" % optplayer)
            return

        playername = soup.find('a', attrs={'class':'btn-split-btn'}).getText().strip()
        table = soup.find('table', attrs={'class':'tablehead'})  # first table.
        headings = table.findAll('tr', attrs={'class':'colhead'})
        rows = table.findAll('tr', attrs={'class': re.compile('^oddrow|^evenrow')})

        seasonlist = [str(i.find('td').string) for i in rows]  # cheap list to find the index for a year.

        if season in seasonlist:
            yearindex = seasonlist.index(season)
        else:
            irc.reply("No season stats found for: %s in %s" % (playername, season))
            return

        heading = headings[0].findAll('td')  # first table, first row is the heading.
        row = rows[yearindex].findAll('td')  # the year comes with the index number, which we find above.

        output = ' | '.join([ircutils.bold(each.text) + ": " + row[i].text for i,each in enumerate(heading)])
        irc.reply("{0} :: {1}".format(self._red(playername), output))

    nflseason = wrap(nflseason, [(getopts({'year': ('int')})), ('text')])

    def nflgamelog(self, irc, msg, args, optlist, optplayer):
        """[--game #] <player>
        Display gamelogs from previous # of games. Ex: Tom Brady
        """

        lookupid = self._playerLookup('eid', optplayer.lower())
        if lookupid == "0":
            irc.reply("No player found for: %s" % optplayer)
            return

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvZ2FtZWxvZy9fL2lk') + '/%s/' % lookupid

        # handle getopts
        optgames = "1"
        if optlist:
            for (key, value) in optlist:
                if key == 'year':  # year, test, optdate if true
                    testdate = self._validate(value, '%Y')
                    if not testdate:
                        irc.reply("Invalid year. Must be YYYY.")
                        return
                    else:
                        url += 'year/%s' % value
                if key == 'games':  # how many games?
                    optgames = value

        # fetch url.
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        # process html, with some error checking.
        soup = BeautifulSoup(html)
        div = soup.find('div', attrs={'class':'mod-container mod-table mod-player-stats'})
        if not div:
            irc.reply("Something broke loading the gamelog. Player might have no stats or gamelog due to position.")
            return
        table = div.find('table', attrs={'class':'tablehead'})
        if not table:
            irc.reply("Something broke loading the gamelog. Player might have no stats or gamelog due to position.")
            return
        stathead = table.find('tr', attrs={'class':'stathead'}).findAll('td')
        header = table.find('tr', attrs={'class':'colhead'}).findAll('td')
        rows = table.findAll('tr', attrs={'class': re.compile('^oddrow.*?|^evenrow.*?')})
        selectedyear = soup.find('select', attrs={'class':'tablesm'}).find('option', attrs={'selected':'selected'})
        # last check before we process the data.
        if len(rows) < 1 or len(header) < 1 or len(stathead) < 1:
            irc.reply("ERROR: I did not find any gamelog data for: %s (Check formatting on gamelog page)." % optplayer)
            return

        # now, lets get to processing the data
        # this is messy but the only way I thought to handle the colspan situation.
        # below, we make a list and iterate in order over stathead tds.
        # statheadlist uses enum to insert, in order found (since the dict gets reordered if you don't)
        # each entry in statheadlist is a dict of colspan:heading, like:
        # {0: {'3': '2012 REGULAR SEASON GAME LOG'}, 1: {'10': 'PASSING'}, 2: {'5': 'RUSHING'}}
        statheaddict = {}
        for e,blah in enumerate(stathead):
            tmpdict = {}
            tmpdict[str(blah['colspan'])] = str(blah.text)
            statheaddict[int(e)] = tmpdict
        # now, we have the statheadlist, create statheadlist to be the list of
        # each header[i] colspan element, where you can use its index value to ref.
        # so, if header[i] = QBR, the "parent" td colspan is PASSING.
        # ex: ['2012 REGULAR SEASON GAME LOG', '2012 REGULAR SEASON GAME LOG',
        # '2012 REGULAR SEASON GAME LOG', 'PASSING', 'PASSING', ... 'RUSHING'
        statheadlist = []
        for q,x in sorted(statheaddict.items()):  # sorted dict, x is the "dict" inside.
            for k,v in x.items():  # key = colspan, v = the td parent header
                for each in range(int(k)):  # range the number to insert.
                    # do some replacement (truncating) because we use this in output.
                    v = v.replace('PASSING','PASS').replace('RUSHING','RUSH').replace('PUNTING','PUNT')
                    v = v.replace('RECEIVING','REC').replace('FUMBLES','FUM').replace('TACKLES','TACK')
                    v = v.replace('INTERCEPTIONS','INT').replace('FIELD GOALS','FG').replace('PATS','XP')
                    v = v.replace('PUNTING','PUNT-')
                    statheadlist.append(v)

        # now, we put all of the data into a data structure
        gamelist = {}  # gamelist dict. one game per entry.
        for i,row in enumerate(rows):  # go through each row and extract, mate with header.
            d = collections.OrderedDict()  # everything in an OD for calc/sort later.
            tds = row.findAll('td')  # all td in each row.
            d['WEEK'] = str(i+1)  # add in the week but +1 for human reference later.
            for f,td in enumerate(tds):  # within each round, there are tds w/data.
                if f > 2:  # the first three will be game log parts, so append statheadlist from above.
                    if str(statheadlist[f]) == str(header[f].getText()):  # check if key is there like INT so we don't double include
                        d[str(header[f].getText())] = str(td.getText())  # this will just look normal like XPM or INT
                    else:  # regular "addtiion" where it is something like FUM-FF
                        d[str(statheadlist[f] + "-" + header[f].getText())] = str(td.getText())
                else:  # td entries 2 and under like DATE, OPP, RESULT
                    d[str(header[f].getText())] = str(td.getText())  # inject all into the OD.
            gamelist[int(i)] = d  # finally, each game and its data in OD now injected into object_list.

        # now, finally, output what we have.
        outputgame = gamelist.get(int(optgames), 'None')

        # handle finding the game or not for output.
        if not outputgame:
            irc.reply("ERROR: I did not find game number {0} in {1}. I did find:".format(optgames, selectedyear.getText()))
            return
        else:  # we did find an outputgame, so go out.
            output = ""
            for k, v in outputgame.items():
                output += "{0}: {1} | ".format(ircutils.bold(k), v)

            irc.reply(output)

    nflgamelog = wrap(nflgamelog, [getopts({'year':('somethingWithoutSpaces'), 'games':('somethingWithoutSpaces')}), ('text')])

Class = NFL

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=250:
