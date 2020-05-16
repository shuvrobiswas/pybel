# -*- coding: utf-8 -*-

"""Convert SBGN-ML files generated by converting CellDesigner files with https://github.com/sbgn/cd2sbgnml.

Inspired by https://github.com/cannin/sbgn2sif.
"""

import itertools as itt
import json
import logging
from collections import defaultdict
from typing import Any, Mapping
from xml.etree import ElementTree  # noqa:S405

import click
import pyobo

from pybel import dsl
from pybel.io.sbgnml.constants import SBGN, XHTML, chebi_name_to_id, hgnc_name_to_id
from pybel.io.sbgnml.utils import _get_label, _iter_references

logger = logging.getLogger(__name__)

DSL_MAPPING = {
    'simple molecule': dsl.Abundance,
    'macromolecule': dsl.Protein,
    'nucleic acid feature': dsl.Rna,
}


def parse(path: str):
    """Parse a SBGN-ML file."""
    tree = ElementTree.parse(path)  # noqa:S314
    return parse_sbgn_tree(tree)


def parse_sbgn_tree(tree: ElementTree.ElementTree) -> Mapping[str, Any]:
    """Parse an SBGN-ML XML element tree."""
    root = tree.getroot()
    maps = list(root.findall(f"{SBGN}map"))
    if 1 < len(maps):
        raise ValueError('not supporting multiple maps in one XML now')
    sbgn_map = maps[0]
    return handle_sbgn_map(sbgn_map)


def handle_sbgn_map(sbgn_map: ElementTree.Element):  # noqa: C901
    """Handle a map element from an SBGN-ML XML element tree."""
    compartments = {}
    for compartment in sbgn_map.findall(f'{SBGN}glyph[@class="compartment"]'):
        compartment_id = compartment.get('id')
        compartment_label = compartment.findall(f'{SBGN}label')[0].get('text')

        g_prefix, g_id, g_name = None, None, None
        if compartment_label:
            _namespaces = ['go', 'mesh']
            g_prefix, g_id, g_name = pyobo.multiground(_namespaces, compartment_label)
            if not g_prefix and not g_id:
                logger.warning(
                    'could not find %s [id=%s] in namespaces %s',
                    compartment_label, compartment_id, _namespaces,
                )

        v = {
            'glyph_id': compartment_id,
            'entity': {
                'prefix': g_prefix,
                'identifier': g_id,
                'name': g_name or compartment_label,
            },
        }

        parent_compartment_id = compartment.get('compartmentRef')
        if parent_compartment_id:
            v['parent'] = parent_compartment_id

        compartments[compartment_id] = v

    port_to_process = {}
    process_to_ports = defaultdict(list)
    process_to_references = defaultdict(list)
    for process in sbgn_map.findall(f'{SBGN}glyph[@class="process"]'):
        process_id = process.get('id')
        process_to_references[process_id].extend(_iter_references(process))
        for port in process.findall(f'{SBGN}port'):
            port_id = port.get('id')
            process_to_ports[process_id].append(port_id)
            if port_id in port_to_process:
                logger.warning('rewriting port %s from %s to %s ', port_id, port_to_process[port_id], process_id)
            port_to_process[port_id] = process_id

    and_to_ports = defaultdict(list)
    ports_to_and = {}
    for and_glyph in sbgn_map.findall(f'{SBGN}glyph[@class="and"]'):
        and_id = and_glyph.get('id')
        for port in and_glyph.findall(f'{SBGN}port'):
            port_id = port.get('id')
            and_to_ports[and_id].append(port_id)
            if port_id in ports_to_and:
                logger.warning('rewriting port %s from %s to %s ', port_id, ports_to_and[port_id], and_id)
            ports_to_and[port_id] = and_id

    # id -> type, id, curie, label, (optional) states, (optional) compartment_id
    glyphs = {}

    # Build up phenotype glyphs
    for phenotype_glyph in sbgn_map.findall(f'{SBGN}glyph[@class="phenotype"]'):
        phenotype_glyph_id = phenotype_glyph.get('id')
        phenotype_glyph_label = _get_label(phenotype_glyph)
        references = _get_references(
            glyph=phenotype_glyph,
            glyph_id=phenotype_glyph_id,
            glyph_class='phenotype',
            glyph_label=phenotype_glyph_label,
            prefixes=['go', 'efo'],
        )
        glyphs[phenotype_glyph_id] = {
            'glyph_id': phenotype_glyph_id,
            'class': 'phenotype',
            'entity': {
                'prefix': references[0][0] if references else None,
                'identifier': references[0][1] if references else None,
                'name': phenotype_glyph_label,
            },
        }

    # Build up normal glyphs (and ones inside complexes)
    for glyph in itt.chain(
        sbgn_map.findall(f'{SBGN}glyph'),
        sbgn_map.findall(f'{SBGN}glyph[@class="complex"]/{SBGN}glyph'),
    ):
        glyph_class = glyph.get('class')
        if glyph_class is None:
            logger.warning('glyph missing class')
            continue
        if glyph_class in {'compartment', 'process', 'and', 'phenotype', 'complex'}:
            continue  # already handled

        if glyph_class not in {'macromolecule', 'simple chemical', 'nucleic acid feature'}:
            logger.warning('unhandled class: %s', glyph_class)
            continue

        glyph_id = glyph.get('id')
        if glyph_id is None:
            logger.warning('glyph missing id')
            continue

        glyph_compartment_id = glyph.get('compartmentRef')
        glyph_compartment = compartments[glyph_compartment_id] if glyph_compartment_id else None

        label = _get_label(glyph)

        states = [
            state.get('value')
            for state in glyph.findall(f'{SBGN}glyph[@class="state variable"]/{SBGN}state')
            if state.get('value')
        ]  # TODO there's also the 'variable' entry which might tell you the position

        info = [
            state.get('text')
            for state in glyph.findall(f'{SBGN}glyph[@class="unit of information"]/{SBGN}label')
            if state.get('text')
        ]

        logger.info(
            '%s %s %s %s %s %s',
            glyph_class,
            glyph_id,
            label,
            f'in {glyph_compartment}' if glyph_compartment else '',
            f'with states: {states}' if states else '',
            f'with info: {info}' if info else '',
        )

        references = _get_references(
            glyph=glyph,
            glyph_id=glyph_id,
            glyph_class=glyph_class,
            glyph_label=label,
            prefixes=['chebi', 'hgnc'],
        )
        if len(references) > 1 and glyph_class == 'macromolecule':
            logger.warning(
                'multiple references for %s [id=%s, class=%s]. Should be a complex?',
                label,
                glyph_id,
                glyph_class,
            )
            glyph_class = 'complex'
        elif len(references) > 1:
            logger.warning(
                '%s %s %s has multiple references',
                glyph_class,
                glyph_id,
                label,
            )
            # TODO handle as complex

        glyphs[glyph_id] = {
            'glyph_id': glyph_id,
            'class': glyph_class,
            'entity': {
                'prefix': references[0][0] if references else None,
                'identifier': references[0][1] if references else None,
                'name': label,
            },
            'states': states,
            'compartment': glyph_compartment,
        }

    # Build up complexes
    for complex_glyph in sbgn_map.findall(f'{SBGN}glyph[@class="complex"]'):
        complex_id = complex_glyph.get('id')
        label = _get_label(complex_glyph)
        component_label_to_info = {}
        for component_label in label.split(':'):
            # make list of endings
            if component_label.endswith('-ubq'):  # ubiquitination
                component_label = component_label[:-len('-ubq')]
                component_identifier = hgnc_name_to_id.get(component_label)
                component_prefix = 'hgnc'
                tag = 'ubq'
            elif component_label.endswith('-P'):  # phosphorylated
                component_label = component_label[:-len('-P')]
                component_identifier = hgnc_name_to_id.get(component_label)
                component_prefix = 'hgnc'
                tag = 'P'
            elif component_label.endswith('*'):  # complex of family of genes with ascending numbers :)
                component_identifier = '?'
                component_prefix = '?'
                tag = None
            elif component_label in {'GTP', 'GDP', 'ATP', 'ADP'}:
                component_identifier = chebi_name_to_id.get(component_label)
                component_prefix = 'chebi'
                tag = None
            else:
                component_identifier = hgnc_name_to_id.get(component_label)
                component_prefix = 'hgnc'
                tag = None

            component_label_to_info[component_label] = {
                # 'glyph_id': ??
                'entity': {
                    'prefix': component_prefix,
                    'identifier': component_identifier,
                    'name': component_label,
                },
                'tags': tag,
            }

        glyphs[complex_id] = {
            'glyph_id': complex_id,
            'class': 'complex',
            'label': label,
            'components': component_label_to_info,
        }

    # Build up arcs to processes
    arcs = {}
    successful = 0
    failures = 0
    for arc in sbgn_map.findall(f'{SBGN}arc'):
        arc_class = arc.get('class')
        if arc_class is None:
            logger.warning('arc has no class')
            failures += 1
            continue
        arc_id = arc.get('id')
        if arc_id is None:
            logger.warning('arc has no id')
            failures += 1
            continue
        arc_source_id = arc.get('source')
        if arc_source_id is None:
            logger.warning('arc:%s has no source', arc_source_id)
            failures += 1
            continue
        if arc_source_id in glyphs:
            arc_source = glyphs[arc_source_id]
        elif (
            arc_source_id in process_to_ports
            or arc_source_id in and_to_ports
        ):
            # FIXME need to differentiate between these two
            arc_source = arc_source_id
        elif arc_source_id in port_to_process:
            arc_source = port_to_process[arc_source_id]
        elif arc_source_id in ports_to_and:
            arc_source = ports_to_and[arc_source_id]
        else:
            logger.warning('can not find source %s', arc_source_id)
            failures += 1
            continue

        arc_target_id = arc.get('target')
        if arc_target_id is None:
            logger.warning('arc:%s has no target', arc_target_id)
            failures += 1
            continue
        if arc_target_id in glyphs:
            arc_target = glyphs[arc_target_id]
        elif (
            arc_target_id in process_to_ports
            or arc_target_id in and_to_ports
        ):
            # FIXME need to differentiate between these two
            arc_target = arc_target_id
        elif arc_target_id in port_to_process:
            arc_target = port_to_process[arc_target_id]
        elif arc_target_id in ports_to_and:
            arc_target = ports_to_and[arc_target_id]
        else:
            logger.warning('can not find target %s', arc_target_id)
            failures += 1
            continue

        successful += 1
        arcs[arc_id] = {
            'class': arc_class,
            'source': arc_source,
            'target': arc_target,
        }

    logger.warning('successful: %d / failure: %d', successful, failures)

    reified_arcs = defaultdict(dict)
    direct_arcs = []

    for arc_id, arc in arcs.items():
        # TODO just put this in previous loop
        arc_class = arc['class']
        arc_source = arc['source']
        arc_target = arc['target']

        if isinstance(arc_source, str) and isinstance(arc_target, dict):
            logger.info('handling %s from %s to %s', arc_class, arc_source, arc_target['glyph_id'])
            reified_arcs[arc_source]['process'] = arc_source
            if 'targets' not in reified_arcs[arc_source]:
                reified_arcs[arc_source]['targets'] = defaultdict(list)
            reified_arcs[arc_source]['targets'][arc_class].append({
                'arc_id': arc_id,
                'arc_class': arc_class,
                'glyph': arc_target,
            })

        elif isinstance(arc_source, dict) and isinstance(arc_target, str):
            logger.info('handling %s from %s to %s', arc_class, arc_source['glyph_id'], arc_target)
            reified_arcs[arc_target]['process'] = arc_target
            if 'sources' not in reified_arcs[arc_target]:
                reified_arcs[arc_target]['sources'] = defaultdict(list)
            reified_arcs[arc_target]['sources'][arc_class].append({
                'arc_id': arc_id,
                'arc_class': arc_class,
                'glyph': arc_source,
            })

        elif isinstance(arc_source, str) and isinstance(arc_target, str):
            logger.warning('unhandled process->process %s', arc_class)

        elif isinstance(arc_source, dict) and isinstance(arc_target, dict):
            logger.info('handling direct %s from %s to %s', arc_class, arc_source['glyph_id'], arc_target['glyph_id'])
            direct_arcs.append({
                'arc_id': arc_id,
                'arc_class': arc_class,
                'source': arc_source,
                'target': arc_target,
            })

        else:
            logger.warning(
                '[%s] unhandled arc class with source=%s target=%s\n%s\n%s',
                arc_class, type(arc_source), type(arc_target),
                arc_source, arc_target
            )

    bodies = sbgn_map.findall(f'{SBGN}notes/{XHTML}html/{XHTML}body')
    try:
        body = bodies[0]
    except IndexError:
        title = None
    else:
        title = body.text.strip()

    return {
        'title': title,
        'reified': list(reified_arcs.values()),
        'direct': direct_arcs,
    }


def _get_references(*, glyph, glyph_id, glyph_label, glyph_class, prefixes):
    references = list(_iter_references(glyph))
    if references:
        return references

    logger.warning(
        'no references for %s [id=%s, class=%s]. Trying grounding with %s',
        glyph_label, glyph_id, glyph_class, prefixes,
    )
    # references = list(_iter_references(glyph))
    g_prefix, g_id, g_name = pyobo.multiground(prefixes, glyph_label)
    if g_prefix:
        return [(g_prefix, g_id)]

    logger.warning(
        'grounding failed for %s [id=%s, class=%s] with %s',
        glyph_label, glyph_id, glyph_class, prefixes,
    )
    return []


@click.command()
def _main():
    import time
    for path in [
        'Apoptosis_VS_SSA_AN.xml.sbgn',
        'COVID19_PAMP_signaling.xml.sbgn',
        'ER_Stress_Cov19.xml.sbgn',
        'HMOX1 pathway.xml.sbgn',
        'Interferon2.xml.sbgn',
    ]:
        click.secho(f'Parsing {path}', bold=True, fg='green')
        rv = parse(path)
        output = path[:-len('.xml.sbgn')]
        with open(f'{output}.json', 'w') as file:
            json.dump(rv, file, indent=2)

        click.echo('sleeping to make sure output is okay')
        time.sleep(1)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.WARNING,
        format='[%(asctime)s] %(levelname)-8s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    _main()
