# Copyright 2014 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Module to interact with Google Directory API."""

import logging

from apiclient.discovery import build
import config
import datastore
import httplib2
from oauth2client import appengine
from oauth2client.client import AccessTokenRefreshError
import setup

from google.appengine.api import memcache
from google.appengine.ext import ndb


API_SERVICE_NAME = 'admin'
DIRECTORY_API_VERSION = 'directory_v1'
MEMCACHE_ADMIN_KEY = 'admins'
MEMCACHE_EXPIRATION_TIME_IN_SECONDS = 600


class SetupNeeded(Exception):
  pass


def _GetAuthorizedHttp(credentials=None):
  """Get the authorized http from the stored credentials.

  The client library will validate and refresh credentials as needed.

  Args:
    credentials: Optional credentials to use instead of any in the datastore.

  Returns:
    authorized http, a "httplib2.Http" instance, with the proper authentication
        header, access token, and credential.

  Raises:
    SetupNeeded: An exception that there are no credentails in the datastore.
  """
  if not credentials:
    credential_storage = appengine.StorageByKeyName(
        appengine.CredentialsModel,
        datastore.CURRENT_DOMAIN,
        'credentials')
    credentials = credential_storage.get()
    if credentials:
      logging.debug('Successfully got credentials from storage.')
    else:
      if config.SERVICE_ACCOUNT:
        credentials = setup.LoadCredentialsFromPem()
      else:
        raise SetupNeeded('Credentials not in storage')

  return credentials.authorize(httplib2.Http())


def BuildService(credentials=None):
  """Build the directory api service.

  Args:
    credentials: Optional credentials to use instead of any in the datastore.

  Returns:
    service object for interacting with the directory api

  Raises:
    Exception: An exception that that the PEM file content is not valid.
  """
  try:
    return build(
        serviceName=API_SERVICE_NAME,
        version=DIRECTORY_API_VERSION,
        http=_GetAuthorizedHttp(credentials))
  except NotImplementedError:
    ndb.Key('CredentialsModel', datastore.CURRENT_DOMAIN).delete()
    raise Exception('The service account credentials are invalid.  '
                    'Check to make sure you have a valid PEM file and you '
                    'have removed any extra data attributes that may have '
                    'been written to the PEM file when converted from '
                    'PKCS12.  The existing PEM key has been revoked and '
                    'needs to be updated with a new valid key.')


def _GetAdminEmails():
  """Get the emails of the members of the admin group.

  Returns:
     admin_emails: Emails of the members of the admin group.
  """
  admin_emails = []
  admin_group_info = BuildService().members().list(
      groupKey=datastore.Setting.get('admin_group')).execute()
  for member in admin_group_info['members']:
    admin_emails.append(member['email'])
  memcache.set(datastore.CURRENT_DOMAIN + ':' + MEMCACHE_ADMIN_KEY,
               admin_emails,
               MEMCACHE_EXPIRATION_TIME_IN_SECONDS)
  return admin_emails


def IsInAdminGroup(user):
  """Determine if the user is a member of the admin group.

  The memcache will be checked first.  If not in memcache, we will then
  make the api call, and then save into memcache for future use.

  Args:
    user: appengine user object

  Returns:
    boolean: True if user is a member of the admin group.  False otherwise.

  Raises:
    Exception: If ADMIN_GROUP is not configured in config.py
    SetupNeeded: If oauth token no longer works.
  """
  try:
    user_info = GetUserInfo(user.email())
  except AccessTokenRefreshError:
    ndb.Key('CredentialsModel', datastore.CURRENT_DOMAIN).delete()
    raise SetupNeeded('oauth token no longer valid')
  # TODO(adhintz) memcache this isAdmin check.
  if user_info.get('isAdmin', ''):
    logging.info('user is a domain admin')
    return True
  logging.debug('Checking if %s is in admin group.', user.nickname())
  if not datastore.Setting.get('admin_group'):
    raise Exception('You must configure ADMIN_GROUP in config.py')
  cached_admin_emails = memcache.get(
      datastore.CURRENT_DOMAIN + ':' + MEMCACHE_ADMIN_KEY)
  if cached_admin_emails is not None:
    logging.debug('Admin info is found in memcache.')
    if user.email() in cached_admin_emails:
      return True
    else:
      return False

  logging.debug('Admin info is not found in memcache.')
  if user.email() in _GetAdminEmails():
    return True

  return False


def GetUserInfo(user_email):
  """Get the user info.

  Args:
    user_email: String of the user email.

  Returns:
    user_info: A dictionary of the user's domain info.
  """
  logging.debug('Getting domain info for %s.', user_email)
  user_info = BuildService().users().get(userKey=user_email).execute()
  return user_info


def UpdateUserInfo(user_email, new_user_info):
  """Updates the user info.

  Args:
    user_email: String of the user email.
    new_user_info: A dictionary of the user's new domain info to be updated.
  """
  logging.debug('Updating domain info for %s.', user_email)
  BuildService().users().update(
      userKey=user_email, body=new_user_info).execute()
