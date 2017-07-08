from __future__ import division, unicode_literals, print_function

"""
Defines wrappers for data sources(magpi, pymatgen etc) for elemental properties.
"""

import os
import json
import re
import six
import abc
from glob import glob
from collections import defaultdict, namedtuple

from monty.design_patterns import singleton

from pymatgen import Element, Composition, Unit
from pymatgen.core.periodic_table import _pt_data, get_el_sp

__author__ = 'Kiran Mathew, Jimin Chen, Logan Ward'

module_dir = os.path.dirname(os.path.abspath(__file__))

# Load elemental cohesive energy data from json file
with open(os.path.join(module_dir, 'data_files', 'cohesive_energies.json'), 'r') as f:
    cohesive_energy_data = json.load(f)


class AbstractData((six.with_metaclass(abc.ABCMeta))):

    @abc.abstractmethod
    def get_property(self, comp, property_name):
        """
        Gets data for a composition object.

        Args:
            comp (Composition/str): composition
            property_name (str): Name of descriptor

        Returns:
            (list): list of values for each atom in comp_obj.
            Note: the returned values are sorted by the corresponding element's electronegativity.
            This is done for the sake of consistency.
        """
        pass


@singleton
class MagpieData(AbstractData):
    """
    Singleton class to get data from Magpie files
    """

    def __init__(self):
        self.all_elemental_props = defaultdict(dict)
        self.available_props = []
        self.data_dir = os.path.join(module_dir, "data_files", 'magpie_elementdata')

        # Make a list of available properties
        for datafile in glob(os.path.join(self.data_dir, "*.table")):
            self.available_props.append(os.path.basename(datafile).replace('.table', ''))

        self._parse()

    def _parse(self):
        """
        parse and store all elemental properties once and for all.
        """
        for descriptor_name in self.available_props:
            with open(os.path.join(self.data_dir, '{}.table'.format(descriptor_name)), 'r') as f:
                lines = f.readlines()
                for atomic_no in range(1, len(_pt_data)+1):  # max Z=103
                    try:
                        if descriptor_name in ["OxidationStates"]:
                            prop_value = [float(i) for i in lines[atomic_no - 1].split()]
                        else:
                            prop_value = float(lines[atomic_no - 1])
                    except ValueError:
                        prop_value = float("NaN")
                    self.all_elemental_props[descriptor_name][str(Element.from_Z(atomic_no))] = prop_value

    def get_property(self, comp, property_name):
        comp = Composition(comp)
        if property_name not in self.available_props:
            raise ValueError("This descriptor is not available from the Magpie repository. "
                             "Choose from {}".format(self.available_props))

        # Get data for given element/compound
        el_amt = comp.get_el_amt_dict()
        # sort symbols by electronegativity
        symbols = sorted(el_amt.keys(), key=lambda sym: get_el_sp(sym).X)

        return [self.all_elemental_props[property_name][el]
                for el in symbols
                for _ in range(int(el_amt[el]))]


class PymatgenData(AbstractData):

    def get_property(self, comp, property_name):
        """
        Get descriptor data for elements in a compound from pymatgen.

        Args:
            comp (str/Composition): Either pymatgen Composition object or string formula,
                eg: "NaCl", "Na+1Cl-1", "Fe2+3O3-2" or "Fe2 +3 O3 -2"
                Notes:
                     - For 'ionic_radii' property, the Composition object must be made of oxidation
                        state decorated Specie objects not the plain Element objects.
                        eg.  fe2o3 = Composition({Specie("Fe", 3): 2, Specie("O", -2): 3})
                     - For string formula, the oxidation state sign(+ or -) must be specified explicitly.
                        eg.  "Fe2+3O3-2"

            property_name (str): pymatgen element attribute name, as defined in the Element class at
                http://pymatgen.org/_modules/pymatgen/core/periodic_table.html

        Returns:
            (list) of values containing descriptor floats for each atom in the compound(sorted by the
                electronegativity of the contituent atoms)

        """
        eldata = []
        # what are these named tuples for? not used or returned! -KM
        eldata_tup_lst = []
        eldata_tup = namedtuple('eldata_tup', 'element propname propvalue propunit amt')

        oxidation_states = {}
        if isinstance(comp, Composition):
            # check whether the composition is composed of oxidation state decorated species (not
            # just plain Elements)
            if hasattr(comp.elements[0], "oxi_state"):
                oxidation_states = dict(
                    [(str(sp.element), sp.oxi_state) for sp in comp.elements])
            el_amt_dict = comp.get_el_amt_dict()
        # string
        else:
            comp, oxidation_states = self.get_composition_oxidation_state(comp)
            el_amt_dict = comp.get_el_amt_dict()

        symbols = sorted(el_amt_dict.keys(), key=lambda sym: get_el_sp(sym).X)

        for el_sym in symbols:

            element = Element(el_sym)
            property_value = None
            property_units = None

            try:
                p = getattr(element, property_name)
            except AttributeError:
                print("{} attribute missing".format(property_name))
                raise

            if p is not None:
                if property_name in ['ionic_radii']:
                    if oxidation_states:
                        property_value = element.ionic_radii[oxidation_states[el_sym]]
                        property_units = Unit("ang")
                    else:
                        raise ValueError(
                            "oxidation state not given for {}; It does not yield a unique "
                            "number per Element".format(property_name))
                else:
                    property_value = float(p)

                # units are None for these pymatgen descriptors
                # todo: there seem to be a lot more unitless descriptors which are not listed here... -Alex D
                if property_name not in ['X', 'Z', 'group', 'row', 'number', 'mendeleev_no',
                                         'ionic_radii']:
                    property_units = p.unit

            # Make a named tuple out of all the available information
            eldata_tup_lst.append(
                eldata_tup(element=el_sym, propname=property_name, propvalue=property_value,
                           propunit=property_units, amt=el_amt_dict[el_sym]))

            # Add descriptor values, one for each atom in the compound
            for i in range(int(el_amt_dict[el_sym])):
                eldata.append(property_value)

        return eldata

    @staticmethod
    def get_composition_oxidation_state(formula):
        """
        Returns the composition and oxidation states from the given formula.
        Formula examples: "NaCl", "Na+1Cl-1",   "Fe2+3O3-2" or "Fe2 +3 O3 -2"

        Args:
            formula (str):

        Returns:
            pymatgen.core.composition.Composition, dict of oxidation states as strings

        """
        oxidation_states_dict = {}
        non_alphabets = re.split('[a-z]+', formula, flags=re.IGNORECASE)
        if not non_alphabets:
            return Composition(formula), oxidation_states_dict
        oxidation_states = []
        for na in non_alphabets:
            s = na.strip()
            if s != "" and ("+" in s or "-" in s):
                digits = re.split('[+-]+', s)
                sign_tmp = re.split('\d+', s)
                sign = [x.strip() for x in sign_tmp if x.strip() != ""]
                oxidation_states.append("{}{}".format(sign[-1], digits[-1].strip()))
        if not oxidation_states:
            return Composition(formula), oxidation_states_dict
        formula_plain = []
        before, after = tuple(formula.split(oxidation_states[0], 1))
        formula_plain.append(before)
        for oxs in oxidation_states[1:]:
            before, after = tuple(after.split(oxs, 1))
            formula_plain.append(before)
        for i, g in enumerate(formula_plain):
            el = re.split("\d", g.strip())[0]
            oxidation_states_dict[str(Element(el))] = int(oxidation_states[i])
        return Composition("".join(formula_plain)), oxidation_states_dict
