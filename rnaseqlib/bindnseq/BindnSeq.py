##
## Class for representing Bind-n-Seq odds ratio/counts data
##
import os
import sys
import time
import glob

import pandas
import numpy as np
import scipy

import rnaseqlib
import rnaseqlib.motif.meme_utils as meme_utils
import rnaseqlib.motif.seq_counter as seq_counter
import rnaseqlib.utils as utils

# Fold change filter for BNS analyses (filter for enrichmed
# kmers)
FC_FILTER = 2.0

def load_bindnseq_or_file(odds_ratio_fname, skiprows=1):
    """
    Load up bindnseq odds ratio file as a pandas DataFrame.
    """
    odds_ratio_df = \
        pandas.read_table(odds_ratio_fname,
                          sep="\t",
                          skiprows=skiprows)
    odds_ratio_df = odds_ratio_df.rename(columns={"#": "kmer"})
    return odds_ratio_df


def load_bindnseq_counts_file(counts_fname, skiprows=1):
    """
    Load up bindnseq counts file as a pandas DataFrame.
    """
    counts_df = None
    with open(counts_fname) as counts_in:
        header = counts_in.readline().strip()
        if header.startswith("#"):
            header = header[1:]
        columns = header.split("\t")
        counts_df = pandas.read_table(counts_fname,
                                      skiprows=skiprows)
        counts_df.columns = columns
    return counts_df


def rescale_score(OldValue, OldMin, OldMax, NewMin, NewMax):
    """
    Rescale values, e.g. convert the value 3 from scale [1,5]
    to new scale like [1,1000].
    """
    return (((OldValue - OldMin) * (NewMax - NewMin)) / (OldMax - OldMin)) + NewMin


class BindnSeq:
    def __init__(self, results_dir, output_dir,
                 label=None):
        """
        Load up results directory
        """
        self.output_dir = output_dir
        self.logger_label = label
        if self.logger_label is None:
            self.logger_label = "BindnSeq"
        self.logger = utils.get_logger(self.logger_label,
                                       self.output_dir)
        self.results_dir = results_dir
        self.label = label
        # All kmer lengths to load
        self.kmer_lens = [4, 5, 6, 7, 8, 9]
        # Odds ratios (DataFrames indexed by kmer length)
        self.odds_ratios = {}
        # Counts (DataFrames indexed by kmer length)
        self.counts = {}


    def load_results(self, results_dir):
        # Load the odds ratios
        self.load_odds_ratios(results_dir)
        # Don't load the counts for now
        #self.load_counts()


    def load_odds_ratios(self, results_dir):
        """
        Load odds ratios.
        """
        self.logger.info("Loading BindnSeq results from: %s" %(results_dir))
        odds_ratio_fnames = glob.glob(os.path.join(results_dir, "*mer_OR"))
        num_or_files = len(odds_ratio_fnames)
        if num_or_files == 0:
            self.logger.critical("No OR files to load!")
            sys.exit(1)
        # Load the odds ratio files
        for or_fname in odds_ratio_fnames:
            kmer_len = int(os.path.basename(or_fname).split("mer")[0])
            odds_ratio_df = load_bindnseq_or_file(or_fname)
            self.odds_ratios[kmer_len] = odds_ratio_df
        self.logger.info("  - Found %d OR files" %(num_or_files))
        return self.odds_ratios


    def get_conc_cols(self, df, suffix='nM'):
        """
        Return concentration columns of a DataFrame. Assume concentration
        columns end in suffix.
        """
        conc_cols = [col for col in df.columns \
                     if col.endswith(suffix)]
        return conc_cols


    def rank_enriched_kmers(self, or_df,
                            rank_col="rank",
                            method="max"):
        """
        Given a DataFrame with OR values, return a new DataFrame
        with kmers sorted according to enrichment. The enrichment
        value will be in a 'rank' column and is computed based on
        the given 'method' argument:

        - method='max': use maximum enrichment across all concentrations
          as enrichment value of a kmer
        - method='mean': use average enrichment across all concentrations
          as enrichment value of a kmer
        """
        ranked_or_df = None
        conc_cols = self.get_conc_cols(or_df)
        ranked_or_df = or_df.copy()
        if method == "max":
            # maximum enrichment method
            # rank the kmers by maximum enrichment across concentrations
            ranked_or_df[rank_col] = or_df[conc_cols].max(axis=1)
        elif method == "mean":
            # mean enrichment method
            # rank the kmers by average enrichment across concentrations
            ranked_or_df[rank_col] = or_df[conc_cols].mean(axis=1)
        else:
            self.logger.criticla("Unknown enrichment method %s" %(method))
            sys.exit(1)
        # Sort resulting DataFrame by rank in descending order
        ranked_or_df.sort(columns=[rank_col],
                          inplace=True,
                          ascending=False)
        # Ordinal ranking
        ranked_or_df["ordinal_rank"] = \
            ranked_or_df["rank"].rank(ascending=False,
                                      method="min")
        return ranked_or_df


    def run_meme_on_enriched_kmers(self, output_dir,
                                   fold_enriched_cutoff=2,
                                   method="max",
                                   len_to_output=None):
        """
        Run MEME on all enriched kmers.
        """
        self.logger.info("Running MEME on enriched BindnSeq kmers...")
        self.logger.info("  - Output dir: %s" %(output_dir))
        self.logger.info("  - Fold enrichment cutoff: %.1f" %(fold_enriched_cutoff))
        self.logger.info("  - Enrichment method: %s" %(method))
        # Make directory for all the kmer sequences to be
        # processed by MEME
        self.seqs_dir = os.path.join(output_dir, "seqs")
        utils.make_dir(self.seqs_dir)
        # Output all enriched kmers to file
        if len_to_output is None:
            len_to_output = "all"
        self.seqs_fname = \
            os.path.join(self.seqs_dir,
                         "enriched_kmers.cutoff_%.1f.method_%s.%s_kmers.fasta" \
                         %(fold_enriched_cutoff, method, str(len_to_output)))
        self.logger.info("Outputting sequences as FASTA to: %s" %(self.seqs_fname))
        seqs_out = open(self.seqs_fname, "w")
        for kmer_len in [4,5,6]:#self.kmer_lens:
            if len_to_output != "all":
                if len_to_output != kmer_len:
                    print "Skipping %d" %(kmer_len)
                    continue
            odds_ratios = self.odds_ratios[kmer_len]
            # Rank the odds ratios
            ranked_ratios = self.rank_enriched_kmers(odds_ratios)
            # Select only the kmers that meet the cutoff
            enriched_ratios = \
                ranked_ratios[ranked_ratios["rank"] >= fold_enriched_cutoff]
            # Write those to file
            for kmer in enriched_ratios["kmer"].values:
                header = ">%s\n" %(kmer)
                seq = "%s\n" %(kmer)
                seqs_out.write(header)
                seqs_out.write(seq)
        seqs_out.close()
        # Run MEME on FASTA file with kmers
        output_dir = os.path.join(output_dir, "meme_output")
        utils.make_dir(output_dir)
        self.logger.info("Running MEME on enriched BindnSeq kmers...")
        self.logger.info("  - MEME output dir: %s" %(output_dir))
        if len(glob.glob(os.path.join(output_dir, "*"))) >= 1:
            self.logger.info("MEME output exists. Skipping...")
            return
        meme_utils.run_meme(self.logger, self.seqs_fname, output_dir)


    def get_fc_cutoff(self, or_df, percentile_cutoff=98):
        """
        Return fold change cutoffs for each kmer length. Select
        cutoff to yield top %X percentile of kmers.
        """
        fc_cutoff = np.percentile(or_df["rank"], percentile_cutoff)
        return fc_cutoff


    def get_enriched_kmers_df(self, kmer_len,
                              method="max"):
        # Load the OR data for this kmer length
        kmer_data = self.odds_ratios[kmer_len]
        # Order kmers by enrichment
        ranked_kmers = self.rank_enriched_kmers(kmer_data, method=method)
        fold_cutoff = self.get_fc_cutoff(ranked_kmers)
        print "  - Fold for %d cutoff: %.2f" %(kmer_len, fold_cutoff)
        # Select only kmers that meet the fold cutoff
        enriched_kmers = ranked_kmers[ranked_kmers["rank"] >= fold_cutoff]
        return enriched_kmers


    def output_enriched_kmers_scores(self, kmer_lens,
                                     region_to_seq_fnames,
                                     output_dir,
                                     method="max"):
        """
        Score enriched kmers in different regions. Calculates the
        sum of number of occurrences of each enriched kmer
        in the region of interest.

        Outputs:
          - flat file format with summary statistics / enrichment
            for kmers in each UTR
          - series of BED-Detail files with positional information for use
            in UCSC

        Parameters:
        -----------

        kmer_lens: list of kmer lengths to score enrichment for
        
        region_to_seq_fnames: mapping from region name (e.g. 3p_utr)
        to FASTA files with their sequences

        method: method to use for fold cutoff across BindnSeq concentrations
        (max uses maximum fold change across all concentrations)
        """
        print "Outputting enriched kmers scores..."
        print "  - Output dir: %s" %(output_dir)
        print "  - Method: %s" %(method)
        utils.make_dir(output_dir)
        if len(self.odds_ratios) == 0:
            raise Exception, "Cannot score enriched motifs since OR data " \
                             "is not loaded."
        print "Scoring enriched motifs for: ", kmer_lens
        for kmer_len in kmer_lens:
            if kmer_len not in self.odds_ratios:
                raise Exception, "Cannot score enriched motifs for k = %d " \
                                 "since data is not loaded." %(kmer_len)
            # Get enriched kmers for this particular kmer len
            enriched_kmers = \
              self.get_enriched_kmers_df(kmer_len, method=method)
            # Make mapping from enriched kmer to its fold change
            enriched_kmer_to_fc = \
              dict(enriched_kmers[["kmer", "rank"]].values)
            print "Total of %d enriched kmers" %(len(enriched_kmers))
            # Load the sequences for the region of interest
            for region in region_to_seq_fnames:
                output_fname = \
                    os.path.join(output_dir,
                                 "enriched_kmers.%s.%d_kmer.txt" \
                                 %(region, kmer_len))
                seq_fname = region_to_seq_fnames[region]
                if seq_fname is None:
                    print "Skipping %s" %(region)
                    continue
                fasta_counter = seq_counter.SeqCounter(seq_fname)
                enriched_kmers_to_score = list(enriched_kmers["kmer"])
                subseq_densities = \
                    fasta_counter.get_subseq_densities(enriched_kmers_to_score)
                # Output enriched kmers ranks (fold change and ordinal)
                fc_rank_str = \
                  ",".join(map(str, list(enriched_kmers["rank"].values)))
                ordinal_rank_str = \
                  ",".join(map(str, list(enriched_kmers["ordinal_rank"].values)))
                subseq_densities["fc_rank"] = fc_rank_str
                subseq_densities["ordinal_rank"] = ordinal_rank_str
                # Compute weighted densities using the fc rank
                subseq_densities = \
                  self.add_rank_weighted_densities(subseq_densities,
                                                   enriched_kmers["rank"].values,
                                                   kmer_len)
                ##
                ## Output summary file
                ##
                print "Outputting summary file to: %s" %(output_fname)
                subseq_densities.to_csv(output_fname,
                                        sep="\t",
                                        float_format="%.4f")
                if region == "cds":
                    cds_per_gene_fname = \
                      "%s.per_gene.txt" %(output_fname.split(".txt")[0])
                    self.output_cds_kmers_per_gene(subseq_densities,
                                                   cds_per_gene_fname,
                                                   kmer_len)
                ##
                ## Output BED files
                ##
                bed_output_fname = \
                    os.path.join(output_dir,
                                 "enriched_kmers.%s.%d_kmer.bed" \
                                 %(region, kmer_len))
                # Description of current track
                curr_track_desc = \
                  "BNS enriched kmers (%s, %d-mer)" %(region, kmer_len)
                self.output_enriched_kmers_as_bed(seq_fname,
                                                  enriched_kmers,
                                                  enriched_kmer_to_fc,
                                                  kmer_len,
                                                  bed_output_fname,
                                                  track_desc=curr_track_desc)


    def output_cds_kmers_per_gene(self, densities_df, output_fname, kmer_len):
        """
        Output enrichment of kmers in CDS per gene (pooling information from
        all CDS exons of a transcript).
        """
        print "Outputting CDS kmers per gene..."
        print "  - Output file: %s" %(output_fname)
        print "  - Kmer length: %d" %(kmer_len)
        # Add transcript IDs to df
        trans_func = lambda name: name.split(";")[2]
        densities_df["transcript_id"] = map(trans_func, densities_df.index)
        # Add gene IDs to df
        gene_func = lambda name: name.split(";")[3]
        densities_df["gene_id"] = map(gene_func, densities_df.index)
        # Calculate the number of starting positions (in KB units)
        # in each exon given kmer length
        one_kb = float(1000)
        print "Calculating number of start positions in KB given kmer"
        densities_df["num_start_pos_in_kb"] = \
          densities_df["seq_len_in_kb"] + (kmer_len/one_kb) + (1/one_kb)
        # Group entries by their transcript ID (e.g. ENST...)
        grouped_df = densities_df.groupby(["gene_id", "transcript_id"])
        # Take the sum of counts across exons per transcript
        kmers_by_transcript = grouped_df.sum_counts.sum().reset_index()
        # Calculate the number of possible starting positions in the
        # transcript, i.e. sum of number of possible starting
        # positions for kmers across exons of the transcript [in kb units]
        kmers_by_transcript["num_starts_in_trans_in_kb"] = \
          grouped_df.num_start_pos_in_kb.sum().reset_index()["num_start_pos_in_kb"]
        # Calculate overall enrichment per transcript, defined as
        # the sum of all the enriched kmers across all exons
        # divided by the sum of possible positions in across all
        # exons [latter in kb units]
        kmers_by_transcript.rename(columns={"sum_counts":
                                            "sum_counts_in_trans"},
                                   inplace=True)
        kmers_by_transcript["trans_density"] = \
          kmers_by_transcript["sum_counts_in_trans"] / \
          kmers_by_transcript["num_starts_in_trans_in_kb"]
        print "COMPLETE ME!"
        ###
        ### Output CDS enrichment information here
        ###
    
        
    def output_enriched_kmers_as_bed(self, seq_fname, 
                                     enriched_kmers,
                                     enriched_kmer_to_fc,
                                     kmer_len,
                                     bed_output_fname,
                                     track_desc="BindnSeq enriched kmers",
                                     db="mm9"):
        """
        Output enriched kmers to the given BED filename as BED.

        Arguments:

          - seq_fname: FASTA sequences to count enriched kmers in
          - enriched_kmers: DataFrame of enriched kmers
          - enriched_kmer_to_fc: dict mapping from enriched kmers to their
            fold change 
        """
        print "Outputting BED file: %s" %(bed_output_fname)
        fasta_counter = seq_counter.SeqCounter(seq_fname)
        if os.path.isfile(bed_output_fname):
            print "Found BED file. Skipping..."
            return
        with open(bed_output_fname, "w") as bed_out:
            # Enriched kmers to look at
            enriched_kmers_to_score = list(enriched_kmers["kmer"])
            # Output BED Detail header
            bed_header = \
              "track name=\"%s\" description=\"%s\" useScore=1 " \
              "db=%s visibility=3" %(track_desc, track_desc, db)
            bed_out.write("%s\n" %(bed_header))
            # Go through all sequences (e.g. these might be 3' UTRs
            # or other genomic features of interest)
            for curr_seq in fasta_counter.seqs:
                seq_name = curr_seq[0][1:]
                seq_len = len(curr_seq[1])
                # Get starting positions of all the enriched kmers in
                # current sequence
                enriched_kmers_starts = \
                  fasta_counter.count_subseqs_with_starts(curr_seq[1],
                                                          enriched_kmers_to_score)
                # Output each enriched kmer start position
                # Parse the sequence chromosome, start, end coordinates
                seq_chrom, seq_coords, seq_strand = \
                  seq_name.split(";")[0].split(":")
                seq_start, seq_end = seq_coords.split("-")
                # Output a BED line for each occurrence of each
                # enriched kmer in current sequence
                for curr_kmer in enriched_kmers_starts:
                    kmer_seq, kmer_starts = curr_kmer
                    # If this kmer has no occurrences in current sequence,
                    # continue to next
                    if len(kmer_starts) == 0:
                        continue
                    kmer_len = len(kmer_seq)
                    for kmer_start in kmer_starts:
                        # Map start to be 1-based not 0 based
                        kmer_start += 1
                        ## BED:
                        ## 1. chrom
                        ## 2. chromStart
                        ## 3. chromEnd
                        ## 4. name
                        ## 5. score
                        ## 6. strand
                        ## 7. thickStart
                        ## 8. thickEnd
                        # Kmer score is defined to be the fold change
                        # rescaled (multiplied by 100 and < 1000)
#                        kmer_score = \
#                          int(min(round(enriched_kmer_to_fc[kmer_seq] * 100.0),
#                              1000))
                        # Rescale fold enrichments assuming maximum
                        # value for fold change
                        fc_ceiling = 4
                        kmer_score = \
                          min(rescale_score(enriched_kmer_to_fc[kmer_seq],
                                            1, fc_ceiling,
                                            1, 1000), 1000)
                        kmer_score = int(kmer_score)
                        # Kmer color
                        kmer_color = \
                          min(rescale_score(enriched_kmer_to_fc[kmer_seq],
                                            1, fc_ceiling,
                                            1, 255), 255)
                        kmer_color = int(kmer_color)
                        if seq_strand == "-":
                            # Minus strand: the start has to be calculated
                            # from the end coordinate
                            kmer_end_in_seq = int(seq_end) - kmer_start + 1
                            kmer_start_in_seq = kmer_end_in_seq - kmer_len 
                        else:
                            continue
                            # Plus strand
                            kmer_start_in_seq = int(seq_start) + kmer_start
                            kmer_end_in_seq = kmer_start_in_seq + kmer_len - 1
                        bed_entry = {"chrom": seq_chrom,
                                     "chromStart": str(kmer_start_in_seq),
                                     "chromEnd": str(kmer_end_in_seq),
                                     "name": kmer_seq,
                                     "score": str(kmer_score),
                                     "strand": seq_strand,
                                     "thickStart": str(kmer_start_in_seq),
                                     "thickEnd": str(kmer_end_in_seq)}
                        bed_line = \
                          "%(chrom)s\t%(chromStart)s\t%(chromEnd)s\t" \
                          "%(name)s\t%(score)s\t%(strand)s\t" \
                          "%(thickStart)s\t%(thickEnd)s\n" \
                          % bed_entry
                        bed_out.write(bed_line)
        print "Completed BED output."


    def parse_counts(self, counts):
        """
        Parse counts field of kmer file.
        """
        return np.array(map(int, counts.split(",")))
    

    def add_rank_weighted_densities(self, subseq_densities,
                                    fc_rank, kmer_len,
                                    min_density_val=2**(-8),
                                    min_seq_len=15):
        """
        Add to the given dataframe of kmer densities additional information,
        namely the *weighted* densities of kmers which are the densities
        multiplied by the rank (fold-change) of the kmer.
        """
        fc_rank = np.array(fc_rank)
        # Record the weighted densities and the maximum
        # fold enrichment of each kmer
        weighted_densities = []
        max_kmer_fcs = []
        for row_num, row in subseq_densities.iterrows():
            # Observed number of counts for each kmer
            obs_counts = self.parse_counts(row["obs_counts"])
            ##
            ## If the kmers are less than the threshold filter, consider them 0
            ##
            # Indices for observed counts where condition is met
            counts_met_inds = np.where(fc_rank >= FC_FILTER)[0]
            counts_not_met_inds = np.where(fc_rank < FC_FILTER)[0]
            if len(counts_met_inds) == 0:
                print "WARNING: Unable to find counts where %.2f FC_FILTER " \
                      "is met" %(FC_FILTER)
            # Where condition is *NOT* met, set counts to 0
            # note the tilde (~)
            obs_counts[counts_not_met_inds] = 0
            # Normalize each observed counts by sequence length (in KB!)
            KB = 1000.0
            len_denom = (float(row["seq_len"]) - kmer_len + 1) / KB
            norm_density = obs_counts / len_denom
            # Multiply the observed counts by the fold change
            # and sum the result
#            weighted_density = np.sum(norm_density * fc_rank)
            weighted_density = np.sum(norm_density)
            weighted_density = max(weighted_density, min_density_val)
            # Record maximum fold change all kmers present in region
            nonzero_kmer_inds = np.where(obs_counts >= 1)[0]
            if len(nonzero_kmer_inds) == 0:
                # There are no enriched kmers in the region
                max_kmer_fc = min_density_val
            else:
                max_kmer_fc = max(fc_rank[nonzero_kmer_inds])
            if row["seq_len"] < min_seq_len:
                # If the length of the region is too short, mark it
                # as zero
                weighted_density = min_density_val
                max_kmer_fc = min_density_val
            weighted_densities.append(weighted_density)
            max_kmer_fcs.append(max_kmer_fc)
        # Record log2_weighted_density
        subseq_densities["log2_weighted_density"] = np.log2(weighted_densities)
        subseq_densities["max_kmer_fc"] = max_kmer_fcs
        return subseq_densities

    
    def __str__(self):
        return "BindnSeq(input_dir=%s)" %(self.results_dir)


    def __repr__(self):
        return self.__str__()
        
        
