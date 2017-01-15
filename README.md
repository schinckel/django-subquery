# django-subquery

Backport of https://docs.djangoproject.com/en/dev/ref/models/expressions/#subquery-expressions to support legacy Django versions.

## Installation:

    $ pip install django-subquery

## Usage:

Please see the official Django documentation: https://docs.djangoproject.com/en/dev/ref/models/expressions/#subquery-expressions

Note that you need to import from ``django_subquery.expressions`` instead of ``django.db.models``.

## Supported versions:

This package has been mildly tested with Django 1.8.

This is only an interim package, until the referenced PR has been merged.

## Unsupported features:

This package is not likely to work on Oracle, as that required some backend within django to work.

## Upgrading from < 1.0

The main class used to be called ``SubQuery``, but was renamed ``Subquery`` when merged into Django 1.11.

You'll need to adjust your code accordingly.