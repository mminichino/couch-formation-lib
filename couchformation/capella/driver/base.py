##
##

import logging
from cbcmgr.cb_capella import Capella
from couchformation.exception import FatalError

logger = logging.getLogger('couchformation.capella.driver.base')
logger.addHandler(logging.NullHandler())


class CapellaDriverError(FatalError):
    pass


class CloudBase(object):

    def __init__(self, parameters: dict):
        self.parameters = parameters
        self.project = parameters.get('project')

        self.cm = Capella()
        try:
            self.capella_project = self.cm.get_project(self.project)
        except Exception as err:
            raise CapellaDriverError(f"can not get Capella project ID: {err}")
        else:
            self.capella_project_id = self.capella_project.get('id')

    def test_session(self):
        try:
            self.cm.list_organizations()
        except Exception as err:
            raise CapellaDriverError(f"not authorized: {err}")

    @property
    def project_id(self):
        return self.capella_project_id
