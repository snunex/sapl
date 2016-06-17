# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2016-04-05 02:09
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('compilacao', '0047_auto_20160330_0027'),
    ]

    operations = [
        migrations.AlterField(
            model_name='dispositivo',
            name='dispositivo_vigencia',
            field=models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dispositivos_vigencias_set', to='compilacao.Dispositivo', verbose_name='Dispositivo de Vigência'),
        ),
    ]