# -*- coding: utf-8 -*-
# Generated by Django 1.9.7 on 2016-09-19 18:03
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parlamentares', '0027_merge'),
        ('sessao', '0024_bloco'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='bloco',
            name='bancadas',
        ),
        migrations.AddField(
            model_name='bloco',
            name='partidos',
            field=models.ManyToManyField(blank=True, to='parlamentares.Partido', verbose_name='Bancadas'),
        ),
    ]
