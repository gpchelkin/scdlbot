from fabric.api import run

def deploy():
    run('uname -s')
    # git clone / pull
    # python setup.py develop
    # service restart
