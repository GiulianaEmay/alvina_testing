from odoo import models, _
from odoo.exceptions import UserError

import logging 

_logger = logging.getLogger(__name__)

class UnlinkMixin(models.AbstractModel):
    _name = 'ab.unlink.mixin'
    _description = 'Control de permisos para unlink'

    def _can_user_unlink(self):
        """Hook reutilizable para validar permisos"""
        user = self.env.user

        # Admin siempre puede
        if user.has_group('base.group_system'):
            return True

        # Validar registros no creados por el usuario
        invalid_records = self.filtered(lambda r: r.create_uid != user)
        if invalid_records:
            return False

        return True

    def unlink(self):
        if not self._can_user_unlink():
            raise UserError(_(
                "No tienes permisos para eliminar estos registros. "
                "Solo el administrador o el creador puede hacerlo."
            ))

        return super().unlink()