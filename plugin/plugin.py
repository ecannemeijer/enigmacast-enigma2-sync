# Enigma2 menu entry. Restarts the sync service only, with a fixed command and no shell.
from Plugins.Plugin import PluginDescriptor
import subprocess


def main(session, **kwargs):
    subprocess.run(["/etc/init.d/enigmacast-sync", "restart"], check=False)


def Plugins(**kwargs):
    return [PluginDescriptor(name="EnigmaCast Sync", description="Profile sync service", where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main)]
