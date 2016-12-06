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


class SubQuery(Expression):
    """
    An explicit subquery. It may contain OuterRef() references to the outer
    query, which will be resolved when it is applied to that query.
    """
    template = '(%(subquery)s)'

    def __init__(self, subquery, output_field=None, **extra):
        self.subquery = subquery.all()
        self.extra = extra
        if output_field is None:
            if len(self.subquery.query.select) == 1:
                output_field = self.subquery.query.select[0].field
        super(SubQuery, self).__init__(output_field)

    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        clone = self.copy()
        clone.is_summary = summarize
        # Copy the subquery, because we will be modifying it.
        clone.subquery = clone.subquery.all()
        clone.subquery.query.bump_prefix(query)

        # Need to recursively resolve these.
        def resolve(child):
            if hasattr(child, 'children'):
                [resolve(_child) for _child in child.children]
            if hasattr(child, 'rhs') and isinstance(child.rhs, F):
                child.rhs = child.rhs.resolve_expression(query, allow_joins, reuse, summarize, for_save)

        resolve(clone.subquery.query.where)
        return clone

    def get_source_expressions(self):
        return [x for x in [getattr(expr, 'lhs', None) for expr in self.subquery.query.where.children] if x]

    def set_source_expressions(self, exprs):
        self.subquery = exprs

    def as_sql(self, compiler, connection, template=None, **extra_context):
        connection.ops.check_expression_support(self)
        template_params = self.extra.copy()
        template_params.update(extra_context)

        template_params['subquery'], sql_params = self.subquery.query.get_compiler(connection=connection)\
                                                                     .as_sql()

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
