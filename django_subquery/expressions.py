from django.db.models.expressions import Expression, F, FieldError
from django.db.models import fields


class ResolvedOuterRef(F):
    """
    An object that contains a reference to an outer query.

    In this case, the reference to the outer query has been resolved, because the
    inner query has been used as a subquery.
    """
    def as_sql(self, *args, **kwargs):
        raise ValueError(
            'This queryset contains a reference to an outer query, and may only be used in a subquery.')

    def _prepare(self, output_field=None):
        return self


class OuterRef(F):
    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        return ResolvedOuterRef(self.name)

    def as_sql(self, *args, **kwargs):
        raise FieldError(
            'This queryset contains an unresolved reference to an outer query, and may not be evaluated.')

    def _prepare(self, output_field=None):
        return self


class SubQuery(Expression):
    """
    An explicit subquery. It may contain OuterRef() references to the outer
    query, which will be resolved when it is applied to that query.
    """
    template = '(%(subquery)s)'

    def __init__(self, subquery, output_field=None, **extra):
        self.subquery = subquery.all()
        self.extra = extra
        if output_field is None and len(self.subquery.query.select) == 1:
            output_field = self.subquery.query.select[0].field
        super(SubQuery, self).__init__(output_field)

    def copy(self):
        clone = super(SubQuery, self).copy()
        # Also create a new copy of the subquery.
        clone.subquery = clone.subquery.all()
        return clone

    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        clone = self.copy()
        clone.is_summary = summarize
        clone.subquery.query.bump_prefix(query)

        # Need to recursively resolve these.
        def resolve_all(child):
            if hasattr(child, 'children'):
                [resolve_all(_child) for _child in child.children]
            if hasattr(child, 'rhs'):
                child.rhs = resolve(child.rhs)

        def resolve(child):
            if hasattr(child, 'resolve_expression'):
                return child.resolve_expression(
                    query=query, allow_joins=allow_joins, reuse=reuse, summarize=summarize,
                    for_save=for_save)
            return child

        resolve_all(clone.subquery.query.where)

        for key, value in clone.subquery.query.annotations.items():
            if isinstance(value, SubQuery):
                clone.subquery.query.annotations[key] = resolve(value)

        return clone

    def get_source_expressions(self):
        return [x for x in [getattr(expr, 'lhs', None) for expr in self.subquery.query.where.children] if x]

    def relabeled_clone(self, change_map):
        clone = self.copy()
        clone.subquery.query = clone.subquery.query.relabeled_clone(change_map)
        clone.subquery.query.external_aliases.update(
            alias for alias in change_map.values() if alias not in clone.subquery.query.tables)
        return clone

    def as_sql(self, compiler, connection, template=None, **extra_context):
        connection.ops.check_expression_support(self)
        template_params = self.extra.copy()
        template_params.update(extra_context)

        template_params['subquery'], sql_params = self.subquery.query.get_compiler(connection=connection).as_sql()

        template = template or template_params.get('template', self.template)
        sql = template % template_params
        sql = connection.ops.unification_cast_sql(self.output_field) % sql
        return sql, sql_params

    def _prepare(self, output_field):
        # If we are the rhs in a subquery, we want to remove the wrapping ()
        if self.template == '(%(subquery)s)':
            clone = self.copy()
            clone.template = '%(subquery)s'
            return clone

        return self


class Exists(SubQuery):
    template = 'EXISTS(%(subquery)s)'

    def __init__(self, *args, **kwargs):
        self.negated = kwargs.pop('negated', False)
        super(Exists, self).__init__(*args, **kwargs)

    def __invert__(self):
        return type(self)(self.subquery, self.output_field, negated=(not self.negated), **self.extra)

    @property
    def output_field(self):
        return fields.BooleanField()

    def resolve_expression(self, query=None, **kwargs):
        # By definition, an EXISTS does not care about the columns, so the query can
        # be simplified down to the primary key.
        self.subquery = self.subquery.values('pk').order_by()
        return super(Exists, self).resolve_expression(query, **kwargs)

    def as_sql(self, compiler, connection, template=None, **extra_context):
        sql, params = super(Exists, self).as_sql(compiler, connection, template, **extra_context)
        if self.negated:
            sql = 'NOT {}'.format(sql)
        return sql, params
