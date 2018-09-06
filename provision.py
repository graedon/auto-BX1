#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug 23 20:00:07 2018

@author: Gray Martin (disaster@fourteenmilecreek.com)
"""

"""
Activate conda:         conda activate provisioning
Run file:               python provision.py

Create executable:      pyinstaller --onefile provision.py
Export conda:           conda env export > provisioning.yml
Import conda:           conda env create -f provisioning.yml
"""

import pickle
import glob
import subprocess as sp
from datetime import date
from pathlib import Path
from textwrap import indent, fill

import git
import serial
from blessings import Terminal
import boto3


TERM = Terminal()
IOT_CLIENT = boto3.client('iot')
DATA_CLIENT = boto3.client('iot-data')
WIDTH = TERM.width - 10

VERBOSE = 0
MOSPATH = "/Users/pfimanufacturing/.mos/bin/mos"
FILEPATH = "/Users/pfimanufacturing/Manufacturing"
DATAPATH = FILEPATH + "/data.pickle"
REPOPATH = FILEPATH + "/BX1"
BUILDPATH = REPOPATH + "/build/fw.zip"
SSID = "SSID"
PASS = "PASS"

def setup():
    """Sets up the program and reads external data, if available."""
    print(TERM.bold + "Command Line Interface" + TERM.normal)
    try:
        update_day = pickle.load(open(DATAPATH, "rb"))
    except (OSError, IOError):
        update_day = date.min
        pickle.dump(update_day, open(DATAPATH, "wb"))
    return update_day

def repo_check(update_day):
    """Ensures that the local copy of the project is up-to-date."""
    print("GitHub Repository Check")
    repo = git.Repo(REPOPATH)
    origin = repo.remotes.origin
    if update_day != date.today():
        try:
            origin.pull()
            update_day = date.today()
            term_good("Repository updated.")
            update_build = 1
        except git.exc.GitCommandError as error:
            term_warning("Unable to connect to Github repository.")
            term_warning(str(error))
            term_warning("Repository not updated.")
            update_build = 0
    else:
        term_good("Repository already up-to-date.")
        update_build = 0
    return update_build

def mos_build(update_build):
    """Builds the firmware from the repository."""
    print("Building from Repository")
    build_file = Path(BUILDPATH)
    if build_file.is_file() and update_build == 0:
        term_good("Updated build found at " + BUILDPATH + ".")
    else:
        result = sp.Popen([MOSPATH + ' build --arch esp32'],
                          shell=True,
                          stdout=sp.PIPE,
                          stderr=sp.PIPE,
                          cwd=REPOPATH)
        output, error = result.communicate()
        # Interestingly, mos build outputs to stderr, even when successful.
        build_out = output.decode('UTF-8')
        build_err = error.decode('UTF-8')

        if VERBOSE == 1:
            term_good(build_out)
            term_good(build_err)

        term_good("Build updated.")

def find_port():
    """Finds connected devices and asks for user input."""
    # Uses pyserial to confirm connection to devices from \dev\tty.*
    print("Finding Devices")
    # The next line is OSX-specific. There are ways around this, though.
    ports = glob.glob("/dev/tty.*")
    result = []
    for port in ports:
        try:
            serial_port = serial.Serial(port)
            serial_port.close()
            result.append(port)
        except (OSError, serial.SerialException):
            pass
    # There should always be one device in the list, but just in case...
    if len(result) > 1:
        term_good("Devices found.")
        def_dev = result[1]
    elif result == []:
        term_warning("No devices found. Make sure your device is connected.")
        return None
    else:
        term_good("One device found.")
        def_dev = result[0]
    # Lists the available devices.
    term_prompt('\n'.join(result))
    # Prompts user to choose a device from the list.
    term_good("Choose device:")
    try:
        choice = int(input(TERM.blue +
                           "   Press enter to use the default port at \n   "
                           + TERM.bold + def_dev + TERM.normal + TERM.blue
                           + " or input a number: "
                           + TERM.normal))
        term_good("Device found at " + result[choice-1])
        return result[choice-1]
    except ValueError:
        term_good("Default device found at " + def_dev)
        return def_dev

def mos_flash(port):
    """Flashes the firmware to the device and prints output.
    Added support to distinguish between good messages and bad messages."""
    print("Flashing Device")
    result = sp.Popen([MOSPATH + ' flash --port '+ port],
                      shell=True,
                      stdout=sp.PIPE,
                      stderr=sp.PIPE,
                      cwd=REPOPATH)
    output, error = result.communicate()
    # I think that successful mos flash output routes through stderr as well.
    flash_out = output.decode('UTF-8')
    flash_err = error.decode('UTF-8')

    if VERBOSE == 1:
        term_warning(flash_out)
        term_warning(flash_err)

    if "All done!" in flash_err:
        term_good("Device flashed successfully.")
    elif "Error:" in flash_err:
        term_warning("Error: previous firmware found. \n" +
                     "Please disconnect device and retry.")
        exit()
    else:
        term_warning("Error: expected message not found.")
        exit()

def aws_provision(port):
    """Provisions the device with AWS and prints output.
    Added support to distinguish between good messages and bad messages."""
    print("Provisioning Device with AWS")
    result = sp.Popen([MOSPATH + ' aws-iot-setup '+ port],
                      shell=True,
                      stdout=sp.PIPE,
                      stderr=sp.PIPE,
                      cwd=REPOPATH)
    output, error = result.communicate()
    prov_out = output.decode('UTF-8')
    prov_err = error.decode('UTF-8')

    if VERBOSE == 1:
        term_warning(prov_out)
        term_warning(prov_err)

    if "Saving and rebooting..." in prov_err:
        term_good("Device provisioned with AWS.")
        prov_list = prov_err.split('\n')
        cert_inf = IOT_CLIENT.describe_thing(thingName=prov_list[54][19:])
    elif "Error:" in prov_err:
        term_warning("Error:" + prov_err)
        exit()
    else:
        term_warning("Error: expected message not found.")
        exit()

    return cert_inf

def aws_findthing(cert_inf):
    """Finds the device information on AWS and updates the shadow."""
    IOT_CLIENT.add_thing_to_thing_group(thingGroupName='BX1-Things',
                                        thingGroupArn='arn:aws:iot:us-east-'+
                                        '1:273069316166:thinggroup/BX1-Things',
                                        thingName=cert_inf['thingName'],
                                        thingArn=cert_inf['thingArn'])
    shadow = """{"state": {"desired":{"state": "ready"}}}"""
    DATA_CLIENT.update_thing_shadow(thingName=cert_inf['thingName'],
                                    payload=shadow)

def mos_wifi(port):
    """Connects the device to WiFi.
    Added support to distinguish between good messages and bad messages."""
    print("Connecting to WiFi")
    result = sp.Popen([MOSPATH + ' wifi '+ SSID + PASS + port],
                      shell=True,
                      stdout=sp.PIPE,
                      stderr=sp.PIPE,
                      cwd=REPOPATH)
    output, error = result.communicate()
    wifi_out = output.decode('UTF-8')
    wifi_err = error.decode('UTF-8')

    if VERBOSE == 1:
        term_warning(wifi_out)
        term_warning(wifi_err)

    if "Saving and rebooting..." in wifi_err:
        term_good("Device connected to WiFi.")
    elif "Error:" in wifi_err:
        term_warning("Error:" + wifi_err)
        exit()
    else:
        term_warning("Error: expected message not found.")
        exit()

def closeout(update_day, cert_inf):
    """Dumps run data to an external file for future use."""
    # term_good("Default Client ID: " + cert_inf['defaultClientId'])
    term_good("Serial Number: " + cert_inf['thingName'])
    # term_good("Thing ID: " + cert_inf['thingId'])
    # term_good("ARN: " + cert_inf['thingArn'])
    with open(DATAPATH, 'wb') as dump_file:
        pickle.dump(update_day, dump_file, pickle.HIGHEST_PROTOCOL)

def term_warning(instring):
    """Formats and prints warnings to the console."""
    instring = instring.splitlines()
    for line in instring:
        line = indent(fill(line, WIDTH), ">\t")
        print(TERM.red + line + TERM.normal)

def term_good(instring):
    """Formats and prints other, better things to the console."""
    instring = instring.splitlines()
    for line in instring:
        line = indent(fill(line, WIDTH), ">\t")
        print(TERM.green + line + TERM.normal)

def term_prompt(instring):
    """Formats and prints a numbered list to the console. It's blue."""
    instring = instring.splitlines()
    linecounter = 1
    for line in instring:
        line = indent(fill(line, WIDTH), "   " + str(linecounter) + ".   ")
        print(TERM.blue + line + TERM.normal)
        linecounter += 1

def process_flow():
    """Bundles up and orders the necessary steps. Clears the terminal."""
    print(TERM.clear(), end='')
    update_day = setup()
    update_build = repo_check(update_day)
    mos_build(update_build)
    port = find_port()
    mos_flash(port)
    cert_inf = aws_provision(port)
    aws_findthing(cert_inf)
    mos_wifi(port)
    closeout(update_day, cert_inf)

def main():
    """Runs process_flow(), possibly many times. Who can say?"""
    process_flow()

if __name__ == "__main__":
    main()
