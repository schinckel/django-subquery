from django.db.models.expressions import Expression, F
from django.db.models import fields


class ResolvedOuterRef(F):
    """
    An object that contains a reference to an outer query.

    In this case, the reference to the outer query has been resolved because
    the inner query has been used as a subquery.
    """
    def as_sql(self, *args, **kwargs):
        raise ValueError(
            'This queryset contains a reference to an outer query and may '
            'only be used in a subquery.'
        )

    def _prepare(self, output_field=None):
        return self


class OuterRef(F):
    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        if isinstance(self.name, self.__class__):
            return self.name
        return ResolvedOuterRef(self.name)

    def _prepare(self, output_field=None):
        return self


class Subquery(Expression):
    """
    An explicit subquery. It may contain OuterRef() references to the outer
    query which will be resolved when it is applied to that query.
    """
    template = '(%(subquery)s)'

    def __init__(self, queryset, output_field=None, **extra):
        self.queryset = queryset
        self.extra = extra
        if output_field is None and len(self.queryset.query.select) == 1:
            output_field = self.queryset.query.select[0].field
        super(Subquery, self).__init__(output_field)

    def copy(self):
        clone = super(Subquery, self).copy()
        clone.queryset = clone.queryset.all()
        return clone

    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        clone = self.copy()
        clone.is_summary = summarize
        clone.queryset.query.bump_prefix(query)

        # Need to recursively resolve these.
        def resolve_all(child):
            if hasattr(child, 'children'):
                [resolve_all(_child) for _child in child.children]
            if hasattr(child, 'rhs'):
                child.rhs = resolve(child.rhs)

        def resolve(child):
            if hasattr(child, 'resolve_expression'):
                resolved = child.resolve_expression(
                    query=query, allow_joins=allow_joins, reuse=reuse,
                    summarize=summarize, for_save=for_save,
                )
                if hasattr(resolved, 'alias'):
                    clone.queryset.query.external_aliases.add(resolved.alias)
                return resolved
            return child

        resolve_all(clone.queryset.query.where)

        for key, value in clone.queryset.query.annotations.items():
            if isinstance(value, Subquery):
                clone.queryset.query.annotations[key] = resolve(value)

        return clone

    def get_source_expressions(self):
        return [
            x for x in [
                getattr(expr, 'lhs', None)
                for expr in self.queryset.query.where.children
            ] if x
        ]

    def relabeled_clone(self, change_map):
        clone = self.copy()
        clone.queryset.query = clone.queryset.query.relabeled_clone(change_map)
        clone.queryset.query.external_aliases.update(
            alias for alias in change_map.values()
            if alias not in clone.queryset.query.tables
        )
        return clone

    def as_sql(self, compiler, connection, template=None, **extra_context):
        connection.ops.check_expression_support(self)
        template_params = self.extra.copy()
        template_params.update(extra_context)
        template_params['subquery'], sql_params = self.queryset.query.get_compiler(connection=connection).as_sql()

        template = template or template_params.get('template', self.template)
        sql = template % template_params
        sql = connection.ops.unification_cast_sql(self.output_field) % sql
        return sql, sql_params

    def _prepare(self, output_field):
        # This method will only be called if this instance is the "rhs" in an
        # expression: the wrapping () must be removed (as the expression that
        # contains this will provide them). SQLite evaluates ((subquery))
        # differently than the other databases.
        if self.template == '(%(subquery)s)':
            clone = self.copy()
            clone.template = '%(subquery)s'
            return clone
        return self


class Exists(Subquery):
    template = 'EXISTS(%(subquery)s)'

    def __init__(self, *args, **kwargs):
        self.negated = kwargs.pop('negated', False)
        super(Exists, self).__init__(*args, **kwargs)

    def __invert__(self):
        return type(self)(self.queryset, self.output_field, negated=(not self.negated), **self.extra)

    @property
    def output_field(self):
        return fields.BooleanField()

    def resolve_expression(self, query=None, **kwargs):
        # As a performance optimization, remove ordering since EXISTS doesn't
        # care about it, just whether or not a row matches.
        self.queryset = self.queryset.order_by()
        return super(Exists, self).resolve_expression(query, **kwargs)

    def as_sql(self, compiler, connection, template=None, **extra_context):
        sql, params = super(Exists, self).as_sql(compiler, connection, template, **extra_context)
        if self.negated:
            sql = 'NOT {}'.format(sql)
        return sql, params

    def as_oracle(self, compiler, connection, template=None, **extra_context):
        # Oracle doesn't allow EXISTS() in the SELECT list, so we must wrap it
        # with a CASE WHEN expression. Since Django's When expression requires
        # a left hand side (column) to compare against, we must change the
        # template ourselves.
        sql, params = self.as_sql(compiler, connection, template, **extra_context)
        sql = 'CASE WHEN {} THEN 1 ELSE 0 END'.format(sql)
        return sql, params
