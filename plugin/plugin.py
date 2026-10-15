# Enigma2 menu entry. Starts and stops the sync service only.
from Plugins.Plugin import PluginDescriptor
import os


def main(session, **kwargs):
    os.system("/etc/init.d/enigmacast-sync restart")


def Plugins(**kwargs):
    return [PluginDescriptor(name="EnigmaCast Sync", description="Profile sync service", where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main)]
