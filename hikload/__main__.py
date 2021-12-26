import collections
import hikload.hikvisionapi as hikvisionapi
from datetime import datetime, timedelta, timezone
import re
import os
import logging
import argparse
import ffmpeg
from tqdm.contrib.logging import logging_redirect_tqdm
import tqdm
from typing import List


class Recording():
    def __init__(self, cid, cname, url, startTime):
        self.cid = cid
        self.cname = cname
        self.url = url
        self.startTime = startTime


def parse_args():
    parser = argparse.ArgumentParser(
        description='Download Recordings from a HikVision server, from a range interval')
    parser.add_argument('server', type=str,
                        help='the hikvision server\'s address')
    parser.add_argument('username', type=str,
                        help='the username')
    parser.add_argument('password', type=str,
                        help='the password')
    parser.add_argument('--starttime', type=lambda s: datetime.fromisoformat(s),
                        default=datetime.now().replace(
                            hour=0, minute=0, second=0, microsecond=0).isoformat(),
                        help='the start time in ISO format (default: today at 00:00:00, local time)')
    parser.add_argument('--endtime', type=lambda s: datetime.fromisoformat(s),
                        default=datetime.now().replace(
                            hour=23, minute=59, second=59, microsecond=0).isoformat(),
                        help='the start time in ISO format (default: today at 23:59:59, local time)')
    parser.add_argument('--folders', type=str, dest="folders", choices=['onepercamera', 'oneperday', 'onepermonth', 'oneperyear'],
                        help='create a separate folder per camera/duration (default: disabled)')
    parser.add_argument('--debug', action=argparse.BooleanOptionalAction, dest="debug",
                        help='enable debug mode (default: false)')
    parser.add_argument('--videoformat', dest="videoformat", default="mp4",
                        help='specify video format (default: mp4)')
    parser.add_argument('--downloads', dest="downloads", default="Downloads",
                        help='the downloads folder (default: "Downloads")')
    parser.add_argument('--frames', dest="frames", type=int,
                        help='save a frame for every X frames in the video (default: false)')
    parser.add_argument('--force', dest="force", type=int,
                        help='force saving of files (default: false)')
    parser.add_argument('--skipseconds', dest="skipseconds", type=int,
                        help='skip first X seconds for each video (default: 0)')
    parser.add_argument('--seconds', dest="seconds", type=int,
                        help='save only X seconds for each video (default: inf)')
    parser.add_argument('--days', dest="days", type=int,
                        help='download videos for the last X days (ignores --endtime and --starttime)')
    parser.add_argument('--skipdownload', dest="skipdownload", action=argparse.BooleanOptionalAction,
                        help='do not download anything')
    parser.add_argument('--allrecordings', dest="allrecordings", action=argparse.BooleanOptionalAction,
                        help='download all recordings saved')
    parser.add_argument('--cameras', dest="cameras", type=lambda s: s.split(","),
                        help='camera IDs to search (example: --cameras=201,301)')
    parser.add_argument('--localtimefilenames', dest="localtimefilenames", action=argparse.BooleanOptionalAction,
                        help='save filenames using date in local time instead of UTC')
    parser.add_argument('--yesterday', dest="yesterday", action=argparse.BooleanOptionalAction,
                        help='download yesterday\'s videos')
    parser.add_argument('--ffmpeg', dest="ffmpeg", action=argparse.BooleanOptionalAction,
                        help='enable ffmpeg and disable downloading directly from server')
    parser.add_argument('--forcetranscoding', dest="forcetranscoding", action=argparse.BooleanOptionalAction,
                        help='force transcoding if downloading directly from server')
    parser.add_argument('--photos', dest="photos", action=argparse.BooleanOptionalAction,
                        help='enable experimental downloading of saved photos')
    parser.add_argument('--mock', dest="mock", action=argparse.BooleanOptionalAction,
                        help='enable mock mode  WARNING! This will not download anything from the server')
    args = parser.parse_args()
    return args


def create_folder_and_chdir(dir):
    path = str(dir)
    if not os.path.exists(path):
        os.makedirs(os.path.normpath(path))
        logging.debug("Created folder %s" % path)
    else:
        logging.debug("Folder %s already exists" % path)
    if os.environ.get('RUNNING_IN_DOCKER') == 'TRUE':
        os.chmod(os.path.normpath(path), 0o777)
    os.chdir(os.path.normpath(path))


def photo_download_from_channel(server: hikvisionapi.HikvisionServer, args, url, filename, cid):
    name = "%s.jpeg" % filename
    logging.info("Started downloading %s" % name)
    logging.debug(
        "Files to download: (url: %r, name: %r)" % (url, name))
    r = server.ContentMgmt.search.downloadURI(url)
    open(name, 'wb').write(r.content)
    if os.environ.get('RUNNING_IN_DOCKER') == 'TRUE':
        os.chmod(name, 0o777)
    logging.info("Finished downloading %s" % name)


def video_download_from_channel(server: hikvisionapi.HikvisionServer, args, url, filename, cid):
    if args.folders:
        name = "%s.%s" % (filename, args.videoformat)
    else:
        name = "%s-%s.%s" % (filename, cid, args.videoformat)
    logging.info("Started downloading %s" % name)
    logging.debug(
        "Files to download: (url: %r, name: %r)" % (url, name))
    if args.frames:
        try:
            url = url.replace(server.host, server.address(
                protocol=False, credentials=True))
            hikvisionapi.downloadRTSPOnlyFrames(
                url, name, debug=args.debug, force=args.force, modulo=args.frames, skipSeconds=args.skipseconds, seconds=args.seconds)
        except ffmpeg.Error:
            logging.error(
                "Could not download %s. Try to remove --frames." % name)
    if args.ffmpeg:
        try:
            url = url.replace(server.host, server.address(
                protocol=False, credentials=True))
            hikvisionapi.downloadRTSP(
                url, name, debug=args.debug, force=args.force, skipSeconds=args.skipseconds, seconds=args.seconds)
        except ffmpeg.Error:
            logging.error(
                "Could not download %s. Try to remove --fmpeg." % name)
    else:
        if args.folders:
            temporaryname = "%s.mp4" % filename
        else:
            temporaryname = "%s-%s.mp4" % (filename, cid)
        r = server.ContentMgmt.search.downloadURI(url)
        open(temporaryname, 'wb').write(r.content)
        try:
            hikvisionapi.processSavedVideo(
                temporaryname, debug=args.debug, skipSeconds=args.skipseconds, seconds=args.seconds,
                fileFormat=args.videoformat, forceTranscode=args.forcetranscoding)
        except ffmpeg.Error:
            logging.error(
                "Could not transcode %s. Try to remove --forcetranscoding." % name)
    if os.environ.get('RUNNING_IN_DOCKER') == 'TRUE':
        os.chmod(name, 0o777)
    logging.info("Finished downloading %s" % name)

def search_for_recordings(server: hikvisionapi.HikvisionServer, args) -> List[Recording]:
    channelList = server.Streaming.getChannels()
    channelids = []
    channels = []
    if args.photos:
        for channel in channelList['StreamingChannelList']['StreamingChannel']:
            if (int(channel['id']) % 10 == 1) and (args.cameras is None or channel['id'] in args.cameras):
                # Force looking at the hidden 103 channel for the photos
                channel['id'] = str(int(channel['id'])+2)
                channelids.append(channel['id'])
                channels.append(channel)
        logging.info("Found channels %s" % channelids)
    else:
        for channel in channelList['StreamingChannelList']['StreamingChannel']:
            if (int(channel['id']) % 10 == 1) and (args.cameras is None or channel['id'] in args.cameras):
                channelids.append(channel['id'])
                channels.append(channel)
        logging.info("Found channels %s" % channelids)

    downloadQueue = []
    for channel in channels:
        cname = channel['channelName']
        cid = channel['id']
        if args.days:
            endtime = datetime.now().replace(
                hour=23, minute=59, second=59, microsecond=0)
            starttime = endtime - timedelta(days=args.days)
        elif args.yesterday:
            endtime = datetime.now().replace(
                hour=23, minute=59, second=59, microsecond=0) - timedelta(days=1)
            starttime = endtime - timedelta(days=1)
        else:
            starttime = args.starttime
            endtime = args.endtime

        logging.debug("Using %s and %s as start and end times" %
                      (starttime.isoformat() + "Z", endtime.isoformat() + "Z"))

        try:
            if args.allrecordings:
                recordings = server.ContentMgmt.search.getAllRecordingsForID(
                    cid)
                logging.info("There are %s recordings in total for channel %s" %
                             (recordings['CMSearchResult']['numOfMatches'], cid))
            else:
                recordings = server.ContentMgmt.search.getPastRecordingsForID(
                    cid, starttime.isoformat() + "Z", endtime.isoformat() + "Z")
                logging.info("Found %s recordings for channel %s" %
                             (recordings['CMSearchResult']['numOfMatches'], cid))
        except hikvisionapi.classes.HikvisionException:
            logging.error("Could not get recordings for channel %s" % cid)
            continue

        # This loops from every recording
        recordinglist = recordings['CMSearchResult']['matchList']['searchMatchItem']
        # In case there is only one recording, we need to make it a list
        if type(recordinglist) is not list:
            recordinglist = [recordinglist]
        result = []
        for i in recordinglist:
            result.append(Recording(
                cid=cid, 
                cname=cname, 
                url=i['mediaSegmentDescriptor']['playbackURI'],
                startTime=i['timeSpan']['startTime']
                ))
            logging.debug("Found recording type %s on channel %s" % (
                i['mediaSegmentDescriptor']['contentType'], cid
            ))
            if not args.photos and i['mediaSegmentDescriptor']['contentType'] != 'video':
                # This recording is not a video, skip it
                continue
        downloadQueue.extend(result)
    return downloadQueue

def search_for_recordings_mock() -> List[Recording]:
    return [
        Recording(cid=1, cname="Channel 1", startTime="2021-12-19T09:04:46Z", url="https://tedyst.ro"),
        Recording(cid=1, cname="Channel 1", startTime="2021-12-19T09:04:46Z", url="https://tedyst.ro"),
        Recording(cid=1, cname="Channel 1", startTime="2021-12-19T09:04:46Z", url="https://tedyst.ro"),
        Recording(cid=1, cname="Channel 1", startTime="2021-12-19T09:04:46Z", url="https://tedyst.ro"),
        Recording(cid=1, cname="Channel 1", startTime="2021-12-19T09:04:46Z", url="https://tedyst.ro"),
        Recording(cid=1, cname="Channel 1", startTime="2021-12-19T09:04:46Z", url="https://tedyst.ro"),
    ]

def download_recordings(server: hikvisionapi.HikvisionServer, args, downloadQueue: List[Recording]):
    original_path = os.path.abspath(os.getcwd())
    for recordingobj in tqdm.tqdm(downloadQueue):
        try:
            os.chdir(original_path)
            recording_time = datetime.strptime(
                recordingobj.startTime, "%Y-%m-%dT%H:%M:%SZ")
            if args.folders:
                create_folder_and_chdir(recordingobj.cname)
                if args.folders in ["oneperyear", "onepermonth", "oneperday"]:
                    create_folder_and_chdir(recording_time.year)
                    if args.folders in ["onepermonth", "oneperday"]:
                        create_folder_and_chdir(recording_time.month)
                        if args.folders in ["oneperday"]:
                            create_folder_and_chdir(recording_time.day)

            # You can choose your own filename, this is just an example
            if args.localtimefilenames:
                date = datetime.strptime(
                    recordingobj.startTime, "%Y-%m-%dT%H:%M:%SZ")
                delta = datetime.now(
                    timezone.utc).astimezone().tzinfo.utcoffset(datetime.now(timezone.utc).astimezone())
                date = date + delta
                name = re.sub(r'[-T\:Z]', '', date.isoformat())
            else:
                name = re.sub(r'[-T\:Z]', '', recordingobj.startTime)

            if not args.skipdownload:
                if args.photos:
                    photo_download_from_channel(
                        server, args, recordingobj.url, name, recordingobj.cid)
                else:
                    video_download_from_channel(
                        server, args, recordingobj.url, name, recordingobj.cid)
            else:
                logging.debug("Skipping download of %s" % recordingobj.url)

            if args.folders:
                os.chdir(original_path)
        except TypeError as e:
            logging.error(
                "HikVision dosen't apparently like to return correct XML data...")
            logging.error(repr(e))
            logging.error(recordingobj)

def main(args):
    server = hikvisionapi.HikvisionServer(
        args.server, args.username, args.password)

    FORMAT = "[%(name)s - %(funcName)20s() ] %(message)s"
    logging.basicConfig(format=FORMAT)
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

    if args.mock:
        downloadQueue = search_for_recordings_mock()
        args.skipdownload = True
    else:
        downloadQueue = search_for_recordings(server, args)
    download_recordings(server, args, downloadQueue)


if __name__ == '__main__':
    args = parse_args()
    try:
        with logging_redirect_tqdm():
            main(args)
    except KeyboardInterrupt:
        logging.info("Exited by user")
        exit(0)
