import hashlib
import logging
import os
import re
from functools import wraps
from operator import itemgetter
from unicodedata import normalize as unicodedata_normalize

import django_filters
import magic
from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Button
from django import forms
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.fields import (GenericForeignKey, GenericRel,
                                                GenericRelation)
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import six, timezone
from django.utils.translation import ugettext_lazy as _
from django_filters.filterset import STRICTNESS
from easy_thumbnails import source_generators
from floppyforms import ClearableFileInput
from reversion.admin import VersionAdmin
from unipath.path import Path

from sapl.crispy_layout_mixin import SaplFormLayout, form_actions, to_row
from sapl.settings import BASE_DIR

sapl_logger = logging.getLogger(BASE_DIR.name)


def pil_image(source, exif_orientation=False, **options):
    return source_generators.pil_image(source, exif_orientation, **options)


def clear_thumbnails_cache(queryset, field):

    for r in queryset:
        assert hasattr(r, field), _(
            'Objeto da listagem não possui o campo informado')

        if not getattr(r, field):
            continue

        path = Path(getattr(r, field).path)

        if not path.exists():
            continue

        cache_files = path.parent.walk()

        for cf in cache_files:
            if cf != path:
                cf.remove()


def normalize(txt):
    return unicodedata_normalize(
        'NFKD', txt).encode('ASCII', 'ignore').decode('ASCII')


def get_settings_auth_user_model():
    return getattr(settings, 'AUTH_USER_MODEL', 'auth.User')


autor_label = '''
    <div class="col-xs-12">
       Autor: <span id="nome_autor" name="nome_autor">
                {% if form.autor.value %}
                    {{form.autor.value}}
                {% endif %}
              </span>
    </div>
'''


autor_modal = '''
   <div id="modal_autor" title="Selecione o Autor" align="center">
       <form>
           <input id="q" type="text" />
           <input id="pesquisar" type="submit" value="Pesquisar"
               class="btn btn-primary btn-sm"/>
       </form>
       <div id="div-resultado"></div>
       <input type="submit" id="selecionar" value="Selecionar"
              hidden="true" />
   </div>
'''


def montar_row_autor(name):
    autor_row = to_row(
        [(name, 0),
         (Button('pesquisar',
                 'Pesquisar Autor',
                 css_class='btn btn-primary btn-sm'), 2),
         (Button('limpar',
                 'Limpar Autor',
                 css_class='btn btn-primary btn-sm'), 10)])

    return autor_row


def montar_helper_autor(self):
    autor_row = montar_row_autor('nome')
    self.helper = FormHelper()
    self.helper.layout = SaplFormLayout(*self.get_layout())

    # Adiciona o novo campo 'autor' e mecanismo de busca
    self.helper.layout[0][0].append(HTML(autor_label))
    self.helper.layout[0][0].append(HTML(autor_modal))
    self.helper.layout[0][1] = autor_row

    # Adiciona espaço entre o novo campo e os botões
    # self.helper.layout[0][4][1].append(HTML('<br /><br />'))

    # Remove botões que estão fora do form
    self.helper.layout[1].pop()

    # Adiciona novos botões dentro do form
    self.helper.layout[0][4][0].insert(2, form_actions(more=[
        HTML('<a href="{{ view.cancel_url }}"'
             ' class="btn btn-inverse">Cancelar</a>')]))


class SaplGenericForeignKey(GenericForeignKey):

    def __init__(
            self,
            ct_field='content_type',
            fk_field='object_id',
            for_concrete_model=True,
            verbose_name=''):
        super().__init__(ct_field, fk_field, for_concrete_model)
        self.verbose_name = verbose_name


class SaplGenericRelation(GenericRelation):
    """
    Extenção da class GenericRelation para implmentar o atributo fields_search

    fields_search é uma tupla de tuplas de dois strings no padrão de construção
        de campos porém com [Field Lookups][ref_1]

        exemplo:
            [No Model Parlamentar em][ref_2] existe a implementação dessa
            classe no atributo autor. Parlamentar possui três informações
            relevantes para buscas realacionadas a Autor:

                - nome_completo;
                - nome_parlamentar; e
                - filiacao__partido__sigla

            que devem ser pesquisados, coincidentemente
            pelo FieldLookup __icontains

            portanto a estrutura de fields_search seria:
                fields_search=(
                    ('nome_completo', '__icontains'),
                    ('nome_parlamentar', '__icontains'),
                    ('filiacao__partido__sigla', '__icontains'),
                )


    [ref_1]: https://docs.djangoproject.com/el/1.10/topics/db/queries/
             #field-lookups
    [ref_2]: https://github.com/interlegis/sapl/blob/master/sapl/
             parlamentares/models.py
    """

    def __init__(self, to, fields_search=(), **kwargs):

        assert 'related_query_name' in kwargs, _(
            'SaplGenericRelation não pode ser instanciada sem '
            'related_query_name.')

        assert fields_search, _(
            'SaplGenericRelation não pode ser instanciada sem fields_search.')

        for field in fields_search:
            # descomente para ver todas os campos que são elementos de busca
            # print(kwargs['related_query_name'], field)

            assert isinstance(field, (tuple, list)), _(
                'fields_search deve ser um array de tuplas ou listas.')

            assert len(field) <= 3, _(
                'cada tupla de fields_search deve possuir até 3 strings')

            # TODO implementar assert para validar campos do Model e lookups

        self.fields_search = fields_search
        super().__init__(to, **kwargs)


class ImageThumbnailFileInput(ClearableFileInput):
    template_name = 'floppyforms/image_thumbnail.html'


class RangeWidgetOverride(forms.MultiWidget):

    def __init__(self, attrs=None):
        widgets = (forms.DateInput(format='%d/%m/%Y',
                                   attrs={'class': 'dateinput',
                                          'placeholder': 'Inicial'}),
                   forms.DateInput(format='%d/%m/%Y',
                                   attrs={'class': 'dateinput',
                                          'placeholder': 'Final'}))
        super(RangeWidgetOverride, self).__init__(widgets, attrs)

    def decompress(self, value):
        if value:
            return [value.start, value.stop]
        return [None, None]

    def format_output(self, rendered_widgets):
        html = '<div class="col-sm-6">%s</div><div class="col-sm-6">%s</div>'\
            % tuple(rendered_widgets)
        return '<div class="row">%s</div>' % html


def register_all_models_in_admin(module_name):
    appname = module_name.split('.')
    appname = appname[1] if appname[0] == 'sapl' else appname[0]
    app = apps.get_app_config(appname)
    for model in app.get_models():
        class CustomModelAdmin(VersionAdmin):
            list_display = [f.name for f in model._meta.fields
                            if f.name != 'id']

        if not admin.site.is_registered(model):
            admin.site.register(model, CustomModelAdmin)


def xstr(s):
    return '' if s is None else str(s)


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def get_base_url(request):
    # TODO substituir por Site.objects.get_current().domain
    # from django.contrib.sites.models import Site

    current_domain = request.get_host()
    protocol = 'https' if request.is_secure() else 'http'
    return "{0}://{1}".format(protocol, current_domain)


def create_barcode(value, width=170, height=50):
    '''
        creates a base64 encoded barcode PNG image
    '''
    from base64 import b64encode
    from reportlab.graphics.barcode import createBarcodeDrawing
    value_bytes = bytes(value, "ascii")
    barcode = createBarcodeDrawing('Code128',
                                   value=value_bytes,
                                   barWidth=width,
                                   height=height,
                                   fontSize=2,
                                   humanReadable=True)
    data = b64encode(barcode.asString('png'))
    return data.decode('utf-8')


YES_NO_CHOICES = [(True, _('Sim')), (False, _('Não'))]

TURNO_TRAMITACAO_CHOICES = [
    ('P', _('Primeiro')),
    ('S', _('Segundo')),
    ('U', _('Único')),
    ('L', _('Suplementar')),
    ('F', _('Final')),
    ('A', _('Votação única em Regime de Urgência')),
    ('B', _('1ª Votação')),
    ('C', _('2ª e 3ª Votação')),
]

INDICADOR_AFASTAMENTO = [
    ('A', _('Afastamento')),
    ('F', _('Fim de Mandato')),
]


def listify(function):
    @wraps(function)
    def f(*args, **kwargs):
        return list(function(*args, **kwargs))
    return f


LISTA_DE_UFS = [
    ('AC', 'Acre'),
    ('AL', 'Alagoas'),
    ('AP', 'Amapá'),
    ('AM', 'Amazonas'),
    ('BA', 'Bahia'),
    ('CE', 'Ceará'),
    ('DF', 'Distrito Federal'),
    ('ES', 'Espírito Santo'),
    ('GO', 'Goiás'),
    ('MA', 'Maranhão'),
    ('MT', 'Mato Grosso'),
    ('MS', 'Mato Grosso do Sul'),
    ('MG', 'Minas Gerais'),
    ('PR', 'Paraná'),
    ('PB', 'Paraíba'),
    ('PA', 'Pará'),
    ('PE', 'Pernambuco'),
    ('PI', 'Piauí'),
    ('RJ', 'Rio de Janeiro'),
    ('RN', 'Rio Grande do Norte'),
    ('RS', 'Rio Grande do Sul'),
    ('RO', 'Rondônia'),
    ('RR', 'Roraima'),
    ('SC', 'Santa Catarina'),
    ('SE', 'Sergipe'),
    ('SP', 'São Paulo'),
    ('TO', 'Tocantins'),
    ('EX', 'Exterior'),
]

RANGE_ANOS = [(year, year) for year in range(timezone.now().year,
                                             1889, -1)]

RANGE_MESES = [
    (1, 'Janeiro'),
    (2, 'Fevereiro'),
    (3, 'Março'),
    (4, 'Abril'),
    (5, 'Maio'),
    (6, 'Junho'),
    (7, 'Julho'),
    (8, 'Agosto'),
    (9, 'Setembro'),
    (10, 'Outubro'),
    (11, 'Novembro'),
    (12, 'Dezembro'),
]

RANGE_DIAS_MES = [(n, n) for n in range(1, 32)]

TIPOS_TEXTO_PERMITIDOS = (
    'application/vnd.oasis.opendocument.text',
    'application/x-vnd.oasis.opendocument.text',
    'application/pdf',
    'application/x-pdf',
    'application/zip',
    'application/acrobat',
    'applications/vnd.pdf',
    'text/pdf',
    'text/x-pdf',
    'text/plain',
    'application/txt',
    'browser/internal',
    'text/anytext',
    'widetext/plain',
    'widetext/paragraph',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/xml',
    'text/xml',
    'text/html',
)

TIPOS_IMG_PERMITIDOS = (
    'image/jpeg',
    'image/jpg',
    'image/jpe_',
    'image/pjpeg',
    'image/vnd.swiftview-jpeg',
    'application/jpg',
    'application/x-jpg',
    'image/pjpeg',
    'image/pipeg',
    'image/vnd.swiftview-jpeg',
    'image/x-xbitmap',
    'image/bmp',
    'image/x-bmp',
    'image/x-bitmap',
    'image/png',
    'application/png',
    'application/x-png',
)


def fabrica_validador_de_tipos_de_arquivo(lista, nome):

    def restringe_tipos_de_arquivo(value):
        if not os.path.splitext(value.path)[1][:1]:
            raise ValidationError(_(
                'Não é possível fazer upload de arquivos sem extensão.'))
        try:
            mime = magic.from_buffer(value.read(), mime=True)
            if mime not in lista:
                raise ValidationError(_('Tipo de arquivo não suportado'))
        except FileNotFoundError:
            raise ValidationError(_('Arquivo não encontrado'))
    # o nome é importante para as migrations
    restringe_tipos_de_arquivo.__name__ = nome
    return restringe_tipos_de_arquivo


restringe_tipos_de_arquivo_txt = fabrica_validador_de_tipos_de_arquivo(
    TIPOS_TEXTO_PERMITIDOS, 'restringe_tipos_de_arquivo_txt')
restringe_tipos_de_arquivo_img = fabrica_validador_de_tipos_de_arquivo(
    TIPOS_IMG_PERMITIDOS, 'restringe_tipos_de_arquivo_img')


def intervalos_tem_intersecao(a_inicio, a_fim, b_inicio, b_fim):
    maior_inicio = max(a_inicio, b_inicio)
    menor_fim = min(a_fim, b_fim)
    return maior_inicio <= menor_fim


class MateriaPesquisaOrderingFilter(django_filters.OrderingFilter):

    choices = (
        ('', 'Selecione'),
        ('dataC', 'Data, Tipo, Ano, Numero - Ordem Crescente'),
        ('dataD', 'Data, Tipo, Ano, Numero - Ordem Decrescente'),
        ('tipoC', 'Tipo, Ano, Numero, Data - Ordem Crescente'),
        ('tipoD', 'Tipo, Ano, Numero, Data - Ordem Decrescente')
    )
    order_by_mapping = {
        '': [],
        'dataC': ['data_apresentacao', 'tipo__sigla', 'ano', 'numero'],
        'dataD': ['-data_apresentacao', '-tipo__sigla', '-ano', '-numero'],
        'tipoC': ['tipo__sigla', 'ano', 'numero', 'data_apresentacao'],
        'tipoD': ['-tipo__sigla', '-ano', '-numero', '-data_apresentacao'],
    }

    def __init__(self, *args, **kwargs):
        kwargs['choices'] = self.choices
        super(MateriaPesquisaOrderingFilter, self).__init__(*args, **kwargs)

    def filter(self, qs, value):
        _value = self.order_by_mapping[value[0]] if value else value
        return super().filter(qs, _value)


class AnoNumeroOrderingFilter(django_filters.OrderingFilter):

    choices = (('DEC', 'Ordem Decrescente'),
               ('CRE', 'Ordem Crescente'),)
    order_by_mapping = {
        'DEC': ['-ano', '-numero'],
        'CRE': ['ano', 'numero'],
    }

    def __init__(self, *args, **kwargs):
        kwargs['choices'] = self.choices
        super(AnoNumeroOrderingFilter, self).__init__(*args, **kwargs)

    def filter(self, qs, value):
        _value = self.order_by_mapping[value[0]] if value else value
        return super().filter(qs, _value)


def gerar_hash_arquivo(arquivo, pk, block_size=2**20):
    md5 = hashlib.md5()
    arq = open(arquivo, 'rb')
    while True:
        data = arq.read(block_size)
        if not data:
            break
        md5.update(data)
    return 'P' + md5.hexdigest() + '/' + pk


class ChoiceWithoutValidationField(forms.ChoiceField):

    def validate(self, value):
        if self.required and not value:
            raise ValidationError(
                self.error_messages['required'], code='required')


def models_with_gr_for_model(model):
    return list(map(
        lambda x: x.related_model,
        filter(
            lambda obj: obj.is_relation and
            hasattr(obj, 'field') and
            isinstance(obj, GenericRel),

            model._meta.get_fields(include_hidden=True))
    ))


def generic_relations_for_model(model):
    """
    Esta função retorna uma lista de tuplas de dois elementos, onde o primeiro
    elemento é um model qualquer que implementa SaplGenericRelation (SGR), o
    segundo elemento é uma lista de todas as SGR's que pode haver dentro do
    model retornado na primeira posição da tupla.

    Exemplo: No Sapl, o model Parlamentar tem apenas uma SGR para Autor.
                Se no Sapl existisse apenas essa SGR, o resultado dessa função
                seria:
                    [   #Uma Lista de tuplas
                        (   # cada tupla com dois elementos
                            sapl.parlamentares.models.Parlamentar,
                            [<sapl.utils.SaplGenericRelation: autor>]
                        ),
                    ]
    """
    return list(map(
        lambda x: (x,
                   list(filter(
                       lambda field: (
                           isinstance(
                               field, SaplGenericRelation) and
                           field.related_model == model),
                       x._meta.get_fields(include_hidden=True)))),
        models_with_gr_for_model(model)
    ))


def texto_upload_path(instance, filename, subpath='', pk_first=False):
    """
    O path gerado por essa função leva em conta a pk de instance.
    isso não é possível naturalmente em uma inclusão pois a implementação
    do django framework chama essa função antes do metodo save

    Por outro lado a forma como vinha sendo formada os paths para os arquivos
    são improdutivas e inconsistentes. Exemplo: usava se o valor de __str__
    do model Proposicao que retornava a descrição da proposição, não retorna
    mais, para uma pasta formar o path do texto_original.
    Ora, o resultado do __str__ citado é totalmente impróprio para ser o nome
    de uma pasta.

    Para colocar a pk no path, a solução encontrada foi implementar o método
    save nas classes que possuem atributo do tipo FileField, implementação esta
    que guarda o FileField em uma variável independente e temporária para savar
    o object sem o arquivo e, logo em seguida, salvá-lo novamente com o arquivo
    Ou seja, nas inclusões que já acomparem um arquivo, haverá dois saves,
    um para armazenar toda a informação e recuperar o pk, e outro logo em
    seguida para armazenar o arquivo.
    """

#    if subpath and '/' not in subpath:
#        subpath = subpath + '/'

    """ TODO: Verifique possibilidade de otimização do código de normalização
    do filename...
    Não use slugify... arquivos,
    geralmente, possuem [.][alguma extensão]
    Slugify retira esse ponto...
    """
    filename = re.sub('[^a-zA-Z0-9.]', '-', filename).strip('-').lower()
    filename = re.sub('[-]+', '-', filename)

    prefix = 'public'

    from sapl.materia.models import Proposicao
    from sapl.protocoloadm.models import DocumentoAdministrativo
    if isinstance(instance, (DocumentoAdministrativo, Proposicao)):
        prefix = 'private'

    str_path = ('./sapl/%(prefix)s/%(model_name)s/'
                '%(subpath)s/%(pk)s/%(filename)s')

    if pk_first:
        str_path = ('./sapl/%(prefix)s/%(model_name)s/'
                    '%(pk)s/%(subpath)s/%(filename)s')

    path = str_path %\
        {
            'prefix': prefix,
            'model_name': instance._meta.model_name,
            'pk': instance.pk,
            'subpath': subpath,
            'filename': filename
        }

    return path


def qs_override_django_filter(self):
    if not hasattr(self, '_qs'):
        valid = self.is_bound and self.form.is_valid()

        if self.is_bound and not valid:
            if self.strict == STRICTNESS.RAISE_VALIDATION_ERROR:
                raise forms.ValidationError(self.form.errors)
            elif bool(self.strict) == STRICTNESS.RETURN_NO_RESULTS:
                self._qs = self.queryset.none()
                return self._qs
                # else STRICTNESS.IGNORE...  ignoring

        # start with all the results and filter from there
        qs = self.queryset.all()
        for name, filter_ in six.iteritems(self.filters):
            value = None
            if valid:
                value = self.form.cleaned_data[name]
            else:
                raw_value = self.form[name].value()
                try:
                    value = self.form.fields[name].clean(raw_value)
                except forms.ValidationError:
                    if self.strict == STRICTNESS.RAISE_VALIDATION_ERROR:
                        raise
                    elif bool(self.strict) == STRICTNESS.RETURN_NO_RESULTS:
                        self._qs = self.queryset.none()
                        return self._qs
                        # else STRICTNESS.IGNORE...  ignoring

            if value is not None:  # valid & clean data
                qs = qs._next_is_sticky()
                qs = filter_.filter(qs, value)

        self._qs = qs

    return self._qs


def filiacao_data(parlamentar, data_inicio, data_fim=None):
    from sapl.parlamentares.models import Filiacao

    filiacoes_parlamentar = Filiacao.objects.filter(
        parlamentar=parlamentar)

    filiacoes = filiacoes_parlamentar.filter(Q(
        data__lte=data_inicio,
        data_desfiliacao__isnull=True) | Q(
        data__lte=data_inicio,
        data_desfiliacao__gte=data_inicio))

    if data_fim:
        filiacoes = filiacoes | filiacoes_parlamentar.filter(
            data__gte=data_inicio,
            data__lte=data_fim)

    return ' | '.join([f.partido.sigla for f in filiacoes])


def parlamentares_ativos(data_inicio, data_fim=None):
    from sapl.parlamentares.models import Mandato, Parlamentar
    '''
    :param data_inicio: define a data de inicial do período desejado
    :param data_fim: define a data final do período desejado
    :return: queryset dos parlamentares ativos naquele período
    '''
    mandatos_ativos = Mandato.objects.filter(Q(
        data_inicio_mandato__lte=data_inicio,
        data_fim_mandato__isnull=True) | Q(
        data_inicio_mandato__lte=data_inicio,
        data_fim_mandato__gte=data_inicio))
    if data_fim:
        mandatos_ativos = mandatos_ativos | Mandato.objects.filter(
            data_inicio_mandato__gte=data_inicio,
            data_inicio_mandato__lte=data_fim)
    else:
        mandatos_ativos = mandatos_ativos | Mandato.objects.filter(
            data_inicio_mandato__gte=data_inicio)

    parlamentares_id = mandatos_ativos.values_list(
        'parlamentar_id',
        flat=True).distinct('parlamentar_id')

    return Parlamentar.objects.filter(id__in=parlamentares_id)


def show_results_filter_set(qr):
    query_params = set(qr.keys())
    if ((len(query_params) == 1 and 'iframe' in query_params) or
            len(query_params) == 0):
        return False

    return True


def sort_lista_chave(lista, chave):
    """
    :param lista: Uma list a ser ordenada .
    :param chave: Algum atributo (chave) que está presente na lista e qual
    deve ser usado para a ordenação da nova
    lista.
    :return: A lista ordenada pela chave passada.
    """
    lista_ordenada = sorted(lista, key=itemgetter(chave))
    return lista_ordenada


def get_mime_type_from_file_extension(filename):
    ext = filename.split('.')[-1]
    if ext == 'odt':
        mime = 'application/vnd.oasis.opendocument.text'
    else:
        mime = "application/%s" % (ext,)
    return mime


def ExtraiTag(texto, posicao):
    for i in range(posicao, len(texto)):
        if (texto[i] == '>'):
            return i + 1


def TrocaTag(texto, startTag, endTag, sizeStart, sizeEnd, styleName):
    textoSaida = ''
    insideTag = 0
    i = 0
    if texto is None or texto.strip() == '':
        return texto
    if '<tbody>' in texto:
        texto = texto.replace('<tbody>', '')
        texto = texto.replace('</tbody>', '')
    while (i < len(texto)):
        shard = texto[i:i + sizeStart]
        if (shard == startTag):
            i = ExtraiTag(texto, i)
            textoSaida += '</para><blockTable style = "' + styleName + '">'
            insideTag = 1
        else:
            if (insideTag == 1):
                if (texto[i:i + sizeEnd] == endTag):
                    textoSaida += 'blockTable><para>'
                    insideTag = 0
                    i = i + sizeEnd
                else:
                    textoSaida += texto[i]
                    i = i + 1
            else:
                textoSaida += texto[i]
                i = i + 1

    return textoSaida
