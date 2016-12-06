# django-subquery

Backport of https://github.com/django/django/pull/6478 to support legacy Django versions.

Installation:

    $ pip install django-subquery

Usage:

Given the model structure below:

    from django.db import models
    from django_subquery.expressions import SubQuery, OuterRef

    class Publisher(models.Model):
        name = models.CharField(max_length=30)
        # ...

    class Author(models.Model):
        name = models.CharField(max_length=200)
        # ...

    class Book(models.Model):
        title = models.CharField(max_length=100)
        authors = models.ManyToManyField('Author')
        publisher = models.ForeignKey(Publisher, on_delete=models.CASCADE)
        publication_date = models.DateField()
        price = models.DecimalField()

We can write some queries to get some really nice results:

    >>> hottest_new_books = Book.objects.filter(publisher=OuterRef('pk')).order_by('-publication_date', '-price')
    >>> Publisher.objects.annotate(hot_title=hottest_new_books.values('title'))
