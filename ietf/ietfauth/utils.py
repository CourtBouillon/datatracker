# Copyright The IETF Trust 2013-2020, All Rights Reserved
# -*- coding: utf-8 -*-


# various authentication and authorization utilities

import datetime

import oidc_provider.lib.claims
from oidc_provider.models import Client as ClientRecord


from functools import wraps

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.db.models import Q
from django.http import HttpResponseRedirect, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils.decorators import available_attrs
from django.utils.http import urlquote

import debug                            # pyflakes:ignore

from ietf.group.models import Role, GroupFeatures
from ietf.person.models import Person

def user_is_person(user, person):
    """Test whether user is associated with person."""
    if not user.is_authenticated or not person:
        return False

    if person.user_id == None:
        return False

    return person.user_id == user.id

def has_role(user, role_names, *args, **kwargs):
    """Determines whether user has any of the given standard roles
    given. Role names must be a list or, in case of a single value, a
    string."""
    if not isinstance(role_names, (list, tuple)):
        role_names = [ role_names ]
    
    if not user or not user.is_authenticated:
        return False

    # use cache to avoid checking the same permissions again and again
    if not hasattr(user, "roles_check_cache"):
        user.roles_check_cache = {}

    key = frozenset(role_names)
    if key not in user.roles_check_cache:
        try:
            person = user.person
        except Person.DoesNotExist:
            return False

        role_qs = {
            "Area Director": Q(person=person, name__in=("pre-ad", "ad"), group__type="area", group__state="active"),
            "Secretariat": Q(person=person, name="secr", group__acronym="secretariat"),
            "IAB" : Q(person=person, name="member", group__acronym="iab"),
            "IANA": Q(person=person, name="auth", group__acronym="iana"),
            "RFC Editor": Q(person=person, name="auth", group__acronym="rfceditor"),
            "ISE" : Q(person=person, name="chair", group__acronym="ise"),
            "IAD": Q(person=person, name="admdir", group__acronym="ietf"),
            "IETF Chair": Q(person=person, name="chair", group__acronym="ietf"),
            "IETF Trust Chair": Q(person=person, name="chair", group__acronym="ietf-trust"),
            "IRTF Chair": Q(person=person, name="chair", group__acronym="irtf"),
            "IAB Chair": Q(person=person, name="chair", group__acronym="iab"),
            "IAB Executive Director": Q(person=person, name="execdir", group__acronym="iab"),
            "IAB Group Chair": Q(person=person, name="chair", group__type="iab", group__state="active"),
            "IAOC Chair": Q(person=person, name="chair", group__acronym="iaoc"),
            "WG Chair": Q(person=person,name="chair", group__type="wg", group__state__in=["active","bof", "proposed"]),
            "WG Secretary": Q(person=person,name="secr", group__type="wg", group__state__in=["active","bof", "proposed"]),
            "RG Chair": Q(person=person,name="chair", group__type="rg", group__state__in=["active","proposed"]),
            "RG Secretary": Q(person=person,name="secr", group__type="rg", group__state__in=["active","proposed"]),
            "AG Secretary": Q(person=person,name="secr", group__type="ag", group__state__in=["active"]),
            "Team Chair": Q(person=person,name="chair", group__type="team", group__state="active"),
            "Program Lead": Q(person=person,name="lead", group__type="program", group__state="active"),
            "Program Secretary": Q(person=person,name="secr", group__type="program", group__state="active"),
            "Program Chair": Q(person=person,name="chair", group__type="program", group__state="active"),
            "Nomcom Chair": Q(person=person, name="chair", group__type="nomcom", group__acronym__icontains=kwargs.get('year', '0000')),
            "Nomcom Advisor": Q(person=person, name="advisor", group__type="nomcom", group__acronym__icontains=kwargs.get('year', '0000')),
            "Nomcom": Q(person=person, group__type="nomcom", group__acronym__icontains=kwargs.get('year', '0000')),
            "Liaison Manager": Q(person=person,name="liaiman",group__type="sdo",group__state="active", ),
            "Authorized Individual": Q(person=person,name="auth",group__type="sdo",group__state="active", ),
            "Recording Manager": Q(person=person,name="recman",group__type="ietf",group__state="active", ),
            "Reviewer": Q(person=person, name="reviewer", group__state="active"),
            "Review Team Secretary": Q(person=person, name="secr", group__reviewteamsettings__isnull=False,group__state="active", ),
            "IRSG Member": (Q(person=person, name="member", group__acronym="irsg") | Q(person=person, name="chair", group__acronym="irtf") | Q(person=person, name="atlarge", group__acronym="irsg")),
            "Robot": Q(person=person, name="robot", group__acronym="secretariat"),
            }

        filter_expr = Q()
        for r in role_names:
            filter_expr |= role_qs[r]

        user.roles_check_cache[key] = bool(Role.objects.filter(filter_expr).exists())

    return user.roles_check_cache[key]


# convenient decorator

def passes_test_decorator(test_func, message):
    """Decorator creator that creates a decorator for checking that
    user passes the test, redirecting to login or returning a 403
    error. The test function should be on the form fn(user) ->
    true/false."""
    def decorate(view_func):
        @wraps(view_func, assigned=available_attrs(view_func))
        def inner(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return HttpResponseRedirect('%s?%s=%s' % (settings.LOGIN_URL, REDIRECT_FIELD_NAME, urlquote(request.get_full_path())))
            elif test_func(request.user, *args, **kwargs):
                return view_func(request, *args, **kwargs)
            else:
                return HttpResponseForbidden(message)
        return inner
    return decorate


def role_required(*role_names):
    """View decorator for checking that the user is logged in and
    has one of the listed roles."""
    return passes_test_decorator(lambda u, *args, **kwargs: has_role(u, role_names, *args, **kwargs),
                                 "Restricted to role%s %s" % ("s" if len(role_names) != 1 else "", ", ".join(role_names)))

# specific permissions

def is_authorized_in_doc_stream(user, doc):
    """Return whether user is authorized to perform stream duties on
    document."""
    if has_role(user, ["Secretariat"]):
        return True

    if not user.is_authenticated:
        return False

    # must be authorized in the stream or group

    if (not doc.stream or doc.stream.slug == "ietf") and has_role(user, ["Area Director"]):
        return True

    if not doc.stream:
        return False

    if doc.stream.slug == "ietf" and doc.group.type_id == "individ":
        return False

    docman_roles = doc.group.features.docman_roles
    if doc.stream.slug == "ietf":
        group_req = Q(group=doc.group)
    elif doc.stream.slug == "irtf":
        group_req = Q(group__acronym=doc.stream.slug) | Q(group=doc.group)
    elif doc.stream.slug == "iab":
        if doc.group.type.slug == 'individ' or doc.group.acronym == 'iab':
            docman_roles = GroupFeatures.objects.get(type_id="iab").docman_roles
        group_req = Q(group__acronym=doc.stream.slug)
    elif doc.stream.slug == "ise":
        if doc.group.type.slug == 'individ':
            docman_roles = GroupFeatures.objects.get(type_id="ietf").docman_roles
        group_req = Q(group__acronym=doc.stream.slug)
    else:
        group_req = Q()

    return Role.objects.filter(Q(name__in=docman_roles, person__user=user) & group_req).exists()

def is_authorized_in_group(user, group):
    """Return whether user is authorized to perform duties on
    a given group."""

    if not user.is_authenticated:
        return False

    if has_role(user, ["Secretariat",]):
        return True

    if group.parent:
        if group.parent.type_id == 'area' and has_role(user, ['Area Director',]):
            return True
        if group.parent.acronym == 'irtf' and has_role(user, ['IRTF Chair',]):
            return True
        if group.parent.acronym == 'iab' and has_role(user, ['IAB','IAB Executive Director',]):
            return True

    return Role.objects.filter(name__in=group.features.groupman_roles, person__user=user,group=group ).exists()

def is_individual_draft_author(user, doc):

    if not user.is_authenticated:
        return False

    if not doc.group.type_id == "individ" :
        return False

    if not hasattr(user, 'person'):
        return False

    if user.person in doc.authors():
        return True

    return False
    
def openid_userinfo(claims, user):
    # Populate claims dict.
    person = get_object_or_404(Person, user=user)
    email = person.email()
    claims.update( {
            'name':         person.plain_name(),
            'given_name':   person.first_name(),
            'family_name':  person.last_name(),
            'nickname':     '-',
            'email':        email.address if email else '',
        } )
    return claims




oidc_provider.lib.claims.StandardScopeClaims.info_profile = (
		'Basic profile',
		'Access to your basic datatracker information: Name.'
	    )

class OidcExtraScopeClaims(oidc_provider.lib.claims.ScopeClaims):

    info_roles = (
            "Datatracker role information",
            "Access to a list of your IETF roles as known by the datatracker"
        )

    def scope_roles(self):
        roles = self.user.person.role_set.values_list('name__slug', 'group__acronym')
        info = {
                'roles': list(roles)
            }
        return info

    info_registration = (
            "IETF Meeting Registration Info",
            "Access to public IETF meeting registration information for the current meeting. "
            "Includes meeting number, affiliation, registration type and ticket type.",
        )

    def scope_registration(self):
        from ietf.meeting.helpers import get_current_ietf_meeting
        from ietf.stats.models import MeetingRegistration
        meeting = get_current_ietf_meeting()
        person = self.user.person
        reg = MeetingRegistration.objects.filter(person=person, meeting=meeting).first()
        if not reg:
            # No person match; try to match by email address.  They could
            # have registered with a new address and added it to the account
            # later.
            email_list = person.email_set.values_list('address')
            reg = MeetingRegistration.objects.filter(email__in=email_list, meeting=meeting).first()
            if reg:
                reg.person = person
                reg.save()
        info = {}
        if reg:
            # maybe register attendence if logged in to follow a meeting
            today = datetime.date.today()
            if meeting.date <= today <= meeting.end_date():
                client = ClientRecord.objects.get(client_id=self.client.client_id)
                if client.name == 'Meetecho' and not reg.attended:
                    reg.attended = True
                    reg.save()
            # fill in info to return
            info = {
                'meeting':      reg.meeting.number,
                # full_week, one_day, student:
                'ticket_type':  reg.ticket_type,
                # in_person, onliine, hackathon:
                'reg_type':     reg.reg_type,
                'affiliation':  reg.affiliation,
            }

        return info
            